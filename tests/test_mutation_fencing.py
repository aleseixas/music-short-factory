"""Exercise public entry points with independent clones and a shared authority."""
import hashlib
import json
import multiprocessing
import os
from pathlib import Path
import shutil
import threading
from unittest.mock import patch

import pytest

from engine.coordination_runtime import acquire_token, release_episode, save_token, sync_authority, sync_channel
from engine.mutation_transaction import fenced_mutation
from engine.pipeline_runtime import prepare_request
from engine.pipeline_state import PipelineError, PipelineStore, atomic_json
from engine.shared_coordination import CoordinationError, SharedCoordinator, using_coordinator
from scripts.pipeline_workflow import queue_episode
from scripts import persist_visual_usage as visual_usage_persistence
from tests.test_shared_coordination import ServerBackend, _queue, authority

pytestmark = pytest.mark.distributed_coordination
PROJECT = Path(__file__).resolve().parents[1]


def seed(root, coordinator):
    path = root / 'episodes/demo/story.json'
    path.parent.mkdir(parents=True)
    path.write_text('{"text":"original"}')
    workflow = root / '.github/workflows/episode-media-preflight.yml'
    workflow.parent.mkdir(parents=True)
    shutil.copyfile(PROJECT / '.github/workflows/episode-media-preflight.yml', workflow)
    store = PipelineStore(root)
    store.start('demo', 'req')
    store.transition('demo', 'UNIQUE')
    store.transition('demo', 'AUTHORING')


@fenced_mutation(root_arg='root')
def write_metadata(root, slug, story=None, assets=None):
    if story is not None:
        (root / 'episodes' / slug / 'story.json').write_bytes(story)
    if assets is not None:
        (root / 'episodes' / slug / 'assets.json').write_bytes(assets)


def visual_usage_payload(recorded_at):
    return {'schema_version': 1, 'episode': 'demo', 'recorded_at': recorded_at, 'visual_usage': []}


@fenced_mutation(root_arg='root')
def write_visual_usage(root, slug, payload):
    target = root / 'episodes' / slug / 'visual_usage.json'
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def sync_index_worker(root, url, operation, entered, resume, started, finished, result):
    """Pause the first process at its index write, after it has read the index."""
    from engine import coordination_runtime as runtime, pipeline_state
    root = Path(root)
    original = pipeline_state.atomic_json

    def paused_write(path, value):
        if entered is not None and Path(path) == root / '.pipeline/state.json':
            entered.set()
            if not resume.wait(15):
                raise TimeoutError('index writer was not resumed')
        return original(path, value)

    try:
        coordinator = SharedCoordinator(root, ServerBackend(url), owner_id='index-reader')
        with using_coordinator(root, coordinator), patch.object(pipeline_state, 'atomic_json', paused_write), \
                patch.object(runtime, 'atomic_json', paused_write, create=True):
            if started is not None:
                started.set()
            if operation == 'authority_default':
                sync_authority(root, 'demo')
            elif operation == 'authority_nostalgia':
                sync_authority(root, 'nostalgia_demo')
            elif operation == 'channel_default':
                sync_channel(root, 'default')
            elif operation == 'channel_nostalgia':
                sync_channel(root, 'nostalgia')
            else:
                raise AssertionError(operation)
        result.put(None)
    except Exception as exc:
        result.put(repr(exc))
    finally:
        finished.set()


def crash_after_shared_commit(root, url):
    root = Path(root)
    coordinator = SharedCoordinator(root, ServerBackend(url), owner_id='author')
    original = coordinator.commit_mutation

    def commit_then_crash(*args, **kwargs):
        original(*args, **kwargs)
        os._exit(23)

    coordinator.commit_mutation = commit_then_crash
    with using_coordinator(root, coordinator):
        write_metadata(root, 'demo', story=b'{"text":"committed"}')


def crash_during_authority_download(root, url):
    root = Path(root)
    coordinator = SharedCoordinator(root, ServerBackend(url), owner_id='author')
    original = Path.write_bytes

    def write_then_crash(path, content):
        count = original(path, content)
        if path.parent == root / 'episodes/demo' and path.name in {'story.json', 'assets.json'}:
            os._exit(24)
        return count

    with using_coordinator(root, coordinator), patch.object(Path, 'write_bytes', write_then_crash):
        sync_authority(root, 'demo', download=True)


def assert_no_stale_metadata_upload(root, url, data, expected):
    coordinator = SharedCoordinator(root, ServerBackend(url), owner_id='author')
    with using_coordinator(root, coordinator):
        sync_authority(root, 'demo')  # A status refresh may not heal stale authored files.
        try:
            write_metadata(root, 'demo')
        except (PipelineError, CoordinationError):
            pass  # Rejecting an out-of-date cache is a valid outcome.
    for name, content in expected.items():
        assert data['files'][f'episodes/demo/{name}'] == content


def paused_preflight(root, url, prepared, resume, result):
    root = Path(root)
    coordinator = SharedCoordinator(root, ServerBackend(url), owner_id='old', lease_seconds=120)
    with using_coordinator(root, coordinator):
        seed(root, coordinator)
        def validator(*args, **kwargs):
            prepared.set()
            assert resume.wait(20)
            return []
        try:
            prepare_request(root, 'demo', 'req', validator=validator)
            result.put('COMMITTED')
        except (CoordinationError, PipelineError) as exc:
            result.put(exc.code)


def test_real_prepare_cannot_finish_after_new_publisher_generation(tmp_path):
    context = multiprocessing.get_context('spawn')
    with authority() as (url, data, lock):
        prepared, resume, result = context.Event(), context.Event(), context.Queue()
        root = tmp_path / 'old'
        worker = context.Process(target=paused_preflight, args=(str(root), url, prepared, resume, result))
        worker.start()
        try:
            assert prepared.wait(15)
            with lock:
                data['now'] = data['states']['default']['lease_expires_at'] + 3
            publisher = SharedCoordinator(tmp_path / 'new', ServerBackend(url), owner_id='new')
            token = _queue(publisher, publisher.acquire('demo', 'req'))
            publisher.close_for_publication(token, 'a' * 64)
            resume.set()
            assert result.get(timeout=15) == 'SHARED_STALE_FENCE'
            worker.join(10)
            assert worker.exitcode == 0
            assert not (root / '.episode-check/demo--req.json').exists()
            assert '.episode-check/demo--req.json' not in data['files']
            assert (root / 'episodes/demo/story.json').read_text() == '{"text":"original"}'
        finally:
            if worker.is_alive():
                worker.terminate()
                worker.join(10)


def test_prepare_commits_validated_receipt_and_fresh_clone_recovers_exact_request(tmp_path):
    with authority() as (url, data, _):
        root = tmp_path / 'first'
        coordinator = SharedCoordinator(root, ServerBackend(url), owner_id='first')
        with using_coordinator(root, coordinator):
            seed(root, coordinator)
            result = prepare_request(root, 'demo', 'media_2', validator=lambda *a, **kw: [])
            assert result['next_action'] == 'wait_for_correlated_run'
            assert Path(result['diagnostics']).is_file()
            receipt = '.episode-check/demo--media_2.json'
            assert json.loads(data['files'][receipt])['request_id'] == 'media_2'
            assert data['states']['default']['request_id'] == 'media_2'
            assert data['files']['episodes/demo/story.json'] == (root / 'episodes/demo/story.json').read_bytes()
        clone = tmp_path / 'second'
        second = SharedCoordinator(clone, ServerBackend(url), owner_id='second')
        with using_coordinator(clone, second):
            sync_authority(clone, 'demo', download=True)
            state = PipelineStore(clone).status('demo')
            assert state['stage'] == 'MEDIA_PREFLIGHT'
            assert (clone / receipt).read_bytes() == data['files'][receipt]
            acquire_token(clone, 'demo')
            PipelineStore(clone).transition('demo', 'READY_TO_QUEUE', run_id='123')
            queue_episode(clone, 'demo', 'media_2')
            assert data['files']['.publish-queue/demo.txt'] == b'demo\n123\nmedia_2\n'
            assert not second.status('demo')['mutation_allowed']


def test_file_changed_while_preparing_never_commits(tmp_path):
    with authority() as (url, data, _):
        coordinator = SharedCoordinator(tmp_path, ServerBackend(url), owner_id='first')
        with using_coordinator(tmp_path, coordinator):
            seed(tmp_path, coordinator)
            def validator(*args, **kwargs):
                (tmp_path / 'episodes/demo/story.json').write_text('changed externally')
                return []
            with pytest.raises(PipelineError, match='LOCAL_MUTATION_CONFLICT'):
                prepare_request(tmp_path, 'demo', 'req', validator=validator)
            assert '.episode-check/demo--req.json' not in data['files']
            assert not (tmp_path / '.episode-check/demo--req.json').exists()


def test_stale_clone_cannot_mutate_until_reconciled(tmp_path):
    with authority() as (url, data, _):
        root = tmp_path / 'first'
        first = SharedCoordinator(root, ServerBackend(url), owner_id='first')
        with using_coordinator(root, first):
            seed(root, first)
            release_episode(root, 'demo')
        second = SharedCoordinator(tmp_path / 'second', ServerBackend(url), owner_id='second')
        current = second.acquire('demo', 'req')
        second.release(current)
        with using_coordinator(root, first):
            with pytest.raises(PipelineError, match='STALE_COORDINATION_GENERATION'):
                prepare_request(root, 'demo', 'req', validator=lambda *a, **kw: [])
            sync_authority(root, 'demo', download=True)
            assert prepare_request(root, 'demo', 'req', validator=lambda *a, **kw: [])['stage'] == 'MEDIA_PREFLIGHT'


def test_stale_idle_index_does_not_authorize_second_slug(tmp_path):
    with authority() as (url, data, _):
        first = SharedCoordinator(tmp_path / 'first', ServerBackend(url), owner_id='first')
        first.acquire('demo', 'req')
        root = tmp_path / 'second'
        second = SharedCoordinator(root, ServerBackend(url), owner_id='second')
        with using_coordinator(root, second):
            atomic_json(root / '.pipeline/state.json', PipelineStore(root)._empty_index())
            with pytest.raises(CoordinationError, match='SHARED_SLOT_OCCUPIED'):
                PipelineStore(root).start('other', 'request_2')
            assert data['states']['default']['slug'] == 'demo'


def test_nonstandard_output_paths_fail_before_private_mutation(tmp_path):
    with authority() as (url, data, _):
        coordinator = SharedCoordinator(tmp_path, ServerBackend(url), owner_id='first')
        with using_coordinator(tmp_path, coordinator):
            atomic_json(tmp_path / 'config/config.json', {'paths': {'output_dir': '../unfenced'}})
            @fenced_mutation(root_arg='root')
            def unsafe(root, slug):
                pytest.fail('unfenced writer must never execute')
            with pytest.raises(PipelineError, match='UNSUPPORTED_COORDINATED_PATHS'):
                unsafe(tmp_path, 'demo')


def test_diagnostic_input_is_copied_into_private_workspace(tmp_path):
    with authority() as (url, data, _):
        coordinator = SharedCoordinator(tmp_path, ServerBackend(url), owner_id='first')
        with using_coordinator(tmp_path, coordinator):
            seed(tmp_path, coordinator)
            diagnostic = tmp_path / 'ci-diagnostics/batch.log'
            diagnostic.parent.mkdir()
            diagnostic.write_text('validated diagnostic')
            @fenced_mutation(root_arg='root')
            def repair(root, slug, diagnostic_log):
                assert diagnostic_log != diagnostic
                assert diagnostic_log.read_text() == 'validated diagnostic'
                (root / 'episodes' / slug / 'story.json').write_text('{"repaired":true}')
            repair(tmp_path, 'demo', diagnostic)
            assert json.loads(data['files']['episodes/demo/story.json']) == {'repaired': True}


def test_actions_prepare_rejects_a_push_request_before_creating_receipt(tmp_path, monkeypatch):
    with authority() as (url, data, _):
        coordinator = SharedCoordinator(tmp_path, ServerBackend(url), owner_id='first')
        with using_coordinator(tmp_path, coordinator):
            monkeypatch.setenv('GITHUB_ACTIONS', 'true')
            with pytest.raises(PipelineError, match='PUSH_REQUEST_REQUIRES_EXTERNAL_TOKEN'):
                prepare_request(tmp_path, 'demo', 'req', validator=lambda *a, **kw: [])
            assert not data['files']


def test_reconcile_does_not_replay_crashed_stale_local_journal(tmp_path):
    with authority() as (url, data, _):
        coordinator = SharedCoordinator(tmp_path, ServerBackend(url), owner_id='first')
        with using_coordinator(tmp_path, coordinator):
            seed(tmp_path, coordinator)
            stale = PipelineStore(tmp_path).status('demo')
            prepare_request(tmp_path, 'demo', 'media_2', validator=lambda *a, **kw: [])
            journal = tmp_path / '.pipeline/transaction.json'
            atomic_json(journal, {'episode': stale, 'index': PipelineStore(tmp_path)._empty_index()})
            sync_authority(tmp_path, 'demo', download=True)
            with PipelineStore(tmp_path).lock():
                state = PipelineStore(tmp_path).status('demo')
            assert state['request_id'] == 'media_2'
            assert state['stage'] == 'MEDIA_PREFLIGHT'
            assert not journal.exists()
            assert list((tmp_path / '.pipeline/conflicts').glob('*-transaction.json'))


def crash_after_initial_claim(root, url):
    root = Path(root)
    coordinator = SharedCoordinator(root, ServerBackend(url), owner_id='crashed')
    with using_coordinator(root, coordinator), patch('engine.coordination_runtime.save_token', side_effect=lambda *a: os._exit(23)):
        PipelineStore(root).start('demo', 'req')


def test_crash_after_initial_claim_fences_old_and_unblocks_new_candidate(tmp_path):
    context = multiprocessing.get_context('spawn')
    with authority() as (url, data, lock):
        worker = context.Process(target=crash_after_initial_claim, args=(str(tmp_path / 'crashed'), url))
        worker.start()
        worker.join(15)
        assert worker.exitcode == 23
        assert data['states']['default']['phase'] == 'CANDIDATE'
        with lock:
            data['now'] = data['states']['default']['lease_expires_at'] + 3
        clone = tmp_path / 'recovered'
        coordinator = SharedCoordinator(clone, ServerBackend(url), owner_id='recovered')
        with using_coordinator(clone, coordinator):
            sync_authority(clone, 'demo', download=True)
            assert PipelineStore(clone).start('fresh', 'new_request')['stage'] == 'CANDIDATE'
            assert coordinator.status('demo')['requested_slug_closed']['reason'] == 'CANCELLED'


def test_reconcile_removes_authoritatively_deleted_metadata_before_next_commit(tmp_path):
    with authority() as (url, data, _):
        root = tmp_path / 'clone'
        coordinator = SharedCoordinator(root, ServerBackend(url), owner_id='first')
        with using_coordinator(root, coordinator):
            seed(root, coordinator)
            _, token = acquire_token(root, 'demo')
            token = coordinator.commit_mutation(token, {'episodes/demo/story.json': b'{"current":true}',
                                                       'episodes/demo/visual_candidates.json': b'{"stale":true}'})
            token = coordinator.commit_mutation(token, {'episodes/demo/visual_candidates.json': None})
            save_token(root, token)
            stale = root / 'episodes/demo/visual_candidates.json'
            stale.write_text('{"stale":true}')
            sync_authority(root, 'demo', download=True)
            assert not stale.exists()
            assert list((root / '.pipeline/conflicts/demo').glob('*-visual_candidates.json'))
            @fenced_mutation(root_arg='root')
            def checkpoint(root, slug):
                return 0
            checkpoint(root, 'demo')
            assert 'episodes/demo/visual_candidates.json' not in data['files']


def test_late_workflow_cannot_acquire_or_relabel_newer_request(tmp_path, monkeypatch):
    from scripts.pipeline_workflow import media_pass
    with authority() as (url, data, _):
        coordinator = SharedCoordinator(tmp_path, ServerBackend(url), owner_id='same_clone')
        with using_coordinator(tmp_path, coordinator):
            seed(tmp_path, coordinator)
            prepare_request(tmp_path, 'demo', 'new_request', validator=lambda *a, **kw: [])
            version = data['states']['default']['version']
            monkeypatch.setenv('GITHUB_ACTIONS', 'true')
            monkeypatch.setenv('PIPELINE_REQUEST_ID', 'old_request')
            with pytest.raises(PipelineError, match='WORKFLOW_REQUEST_STALE'):
                acquire_token(tmp_path, 'demo')
            with pytest.raises(PipelineError, match='WORKFLOW_REQUEST_STALE'):
                media_pass(tmp_path, 'demo', 'old_request')
            assert data['states']['default']['version'] == version
            assert data['states']['default']['request_id'] == 'new_request'


def test_generic_metadata_checkpoint_cannot_approve_an_unvalidated_bundle(tmp_path):
    from tests.test_publication_snapshot_recovery import make_bundle
    from publishing.snapshot import approved_manifest
    from publishing.base import PublishingError
    with authority() as (url, data, _):
        coordinator = SharedCoordinator(tmp_path, ServerBackend(url), owner_id='author')
        with using_coordinator(tmp_path, coordinator):
            seed(tmp_path, coordinator)
            make_bundle(tmp_path)
            @fenced_mutation(root_arg='root')
            def checkpoint(root, slug):
                return 0
            checkpoint(tmp_path, 'demo')
            artifact = json.loads(data['files']['.pipeline/artifacts/demo.json'])
            assert artifact['bundle_approved'] is False
            _, token = acquire_token(tmp_path, 'demo')
            token = coordinator.change_request(token, 'request_1')
            _queue(coordinator, token)
            with pytest.raises(PublishingError, match='SNAPSHOT_NOT_APPROVED'):
                approved_manifest(tmp_path, 'demo', '123', 'request_1')


def test_bundle_gate_commits_its_validated_manifest_with_the_fence():
    from tests.test_publish_ready_bundle import PublishReadyBundleTestCase, EPISODE, RUN_ID
    from publishing.snapshot import approved_manifest
    fixture = PublishReadyBundleTestCase()
    fixture.setUp()
    try:
        with authority() as (url, data, _):
            coordinator = SharedCoordinator(fixture.root, ServerBackend(url), owner_id='author')
            with using_coordinator(fixture.root, coordinator), patch.dict(os.environ, {'PIPELINE_REQUEST_ID': 'req'}):
                save_token(fixture.root, coordinator.acquire(EPISODE, 'req'))
                fixture.create()
                artifact = json.loads(data['files'][f'.pipeline/artifacts/{EPISODE}.json'])
                assert artifact['bundle_approved'] is True
                _, token = acquire_token(fixture.root, EPISODE)
                _queue(coordinator, token, RUN_ID)
                approved_manifest(fixture.root, EPISODE, RUN_ID, 'req')
    finally:
        fixture.tearDown()


def test_status_refresh_preserves_local_draft_and_recovers_exact_receipt(tmp_path):
    from engine.coordination_runtime import sync_channel
    with authority() as (url, data, _):
        coordinator = SharedCoordinator(tmp_path, ServerBackend(url), owner_id='author')
        with using_coordinator(tmp_path, coordinator):
            seed(tmp_path, coordinator)
            prepare_request(tmp_path, 'demo', 'req', validator=lambda *a, **kw: [])
            draft = tmp_path / 'episodes/demo/story.json'
            draft.write_text('{"unsaved_draft":true}')
            (tmp_path / '.episode-check/demo--req.json').unlink()
            sync_channel(tmp_path, 'default')
            assert draft.read_text() == '{"unsaved_draft":true}'
            assert PipelineStore(tmp_path).status('demo')['stage'] == 'MEDIA_PREFLIGHT'
            assert (tmp_path / '.episode-check/demo--req.json').is_file()


@pytest.mark.parametrize('first,second,remote_slug,initial,expected', [
    ('authority_default', 'channel_nostalgia', 'demo',
     {'default': None, 'nostalgia': 'nostalgia_old'}, {'default': 'demo', 'nostalgia': None}),
    ('channel_default', 'authority_nostalgia', 'nostalgia_demo',
     {'default': 'old', 'nostalgia': None}, {'default': None, 'nostalgia': 'nostalgia_demo'}),
])
def test_independent_channel_syncs_merge_local_index(tmp_path, first, second, remote_slug, initial, expected):
    context = multiprocessing.get_context('spawn')
    with authority() as (url, _, _):
        SharedCoordinator(tmp_path / 'remote', ServerBackend(url), owner_id='remote').acquire(remote_slug, 'req')
        root = tmp_path / 'shared_checkout'
        store = PipelineStore(root)
        index = store._empty_index()
        index['active'] = initial
        atomic_json(store.index_path, index)
        entered, resume, started, first_done, second_done = (context.Event() for _ in range(5))
        first_result, second_result = context.Queue(), context.Queue()
        first_process = context.Process(target=sync_index_worker,
            args=(str(root), url, first, entered, resume, None, first_done, first_result))
        second_process = context.Process(target=sync_index_worker,
            args=(str(root), url, second, None, None, started, second_done, second_result))
        first_process.start()
        try:
            assert entered.wait(15), 'first sync did not reach its index write'
            second_process.start()
            assert started.wait(15), 'second sync did not start'
            second_done.wait(2)  # It can finish here only if the first index read was outside the lock.
            resume.set()
            for process in (first_process, second_process):
                process.join(15)
                assert process.exitcode == 0
            assert first_result.get(timeout=5) is None
            assert second_result.get(timeout=5) is None
            assert json.loads(store.index_path.read_text())['active'] == expected
        finally:
            resume.set()
            for process in (first_process, second_process):
                if process.is_alive():
                    process.terminate()
                    process.join(10)


def test_lease_heartbeat_runs_during_initial_snapshot_copy_and_post_prepare_hash(tmp_path):
    from engine import mutation_transaction as transaction
    with authority() as (url, data, _):
        coordinator = SharedCoordinator(tmp_path, ServerBackend(url), owner_id='author', lease_seconds=3)
        with using_coordinator(tmp_path, coordinator):
            seed(tmp_path, coordinator)
            stages = {name: threading.Event() for name in ('initial_hash', 'copy', 'prepare', 'post_hash')}
            current = [None]
            initial_seen = [False]
            original_heartbeat = coordinator.heartbeat
            original_selected = transaction._selected
            original_copy = transaction._copy_inputs

            def heartbeat(token):
                renewed = original_heartbeat(token)
                if current[0] is not None:
                    stages[current[0]].set()
                return renewed

            def await_heartbeat(name):
                current[0] = name
                assert stages[name].wait(4), f'lease heartbeat stopped during {name}'
                current[0] = None

            def selected(root, slug):
                if Path(root).resolve() == tmp_path.resolve():
                    if not initial_seen[0]:
                        initial_seen[0] = True
                        await_heartbeat('initial_hash')
                else:
                    await_heartbeat('post_hash')
                return original_selected(root, slug)

            def copy_inputs(root, staging, slug):
                await_heartbeat('copy')
                return original_copy(root, staging, slug)

            @fenced_mutation(root_arg='root')
            def prepare(root, slug):
                await_heartbeat('prepare')
                (root / 'episodes' / slug / 'story.json').write_bytes(b'{"text":"prepared"}')

            with patch.object(coordinator, 'heartbeat', heartbeat), \
                    patch.object(transaction, '_selected', selected), patch.object(transaction, '_copy_inputs', copy_inputs):
                prepare(tmp_path, 'demo')
            assert all(event.is_set() for event in stages.values())
            assert data['files']['episodes/demo/story.json'] == b'{"text":"prepared"}'


def test_crash_after_shared_cas_cannot_reupload_old_local_metadata(tmp_path):
    context = multiprocessing.get_context('spawn')
    with authority() as (url, data, _):
        root = tmp_path / 'checkout'
        coordinator = SharedCoordinator(root, ServerBackend(url), owner_id='author')
        with using_coordinator(root, coordinator):
            seed(root, coordinator)
            write_metadata(root, 'demo')  # Establish a fully applied local snapshot.
        worker = context.Process(target=crash_after_shared_commit, args=(str(root), url))
        worker.start()
        worker.join(20)
        assert worker.exitcode == 23
        expected = {'story.json': b'{"text":"committed"}'}
        assert data['files']['episodes/demo/story.json'] == expected['story.json']
        assert (root / 'episodes/demo/story.json').read_bytes() == b'{"text":"original"}'
        assert_no_stale_metadata_upload(root, url, data, expected)


def test_crash_mid_download_cannot_reupload_partly_reconciled_metadata(tmp_path):
    context = multiprocessing.get_context('spawn')
    with authority() as (url, data, _):
        root = tmp_path / 'checkout'
        coordinator = SharedCoordinator(root, ServerBackend(url), owner_id='author')
        with using_coordinator(root, coordinator):
            seed(root, coordinator)
            write_metadata(root, 'demo', assets=b'{"asset":"original"}')
        clone = tmp_path / 'newer_clone'
        newer = SharedCoordinator(clone, ServerBackend(url), owner_id='author')
        expected = {'assets.json': b'{"asset":"committed"}', 'story.json': b'{"text":"committed"}'}
        with using_coordinator(clone, newer):
            sync_authority(clone, 'demo', download=True)
            write_metadata(clone, 'demo', story=expected['story.json'], assets=expected['assets.json'])
        worker = context.Process(target=crash_during_authority_download, args=(str(root), url))
        worker.start()
        worker.join(20)
        assert worker.exitcode == 24
        local = {name: (root / 'episodes/demo' / name).read_bytes() for name in expected}
        assert sum(local[name] == expected[name] for name in expected) == 1
        assert_no_stale_metadata_upload(root, url, data, expected)


@pytest.mark.parametrize('local_time,remote_time', [
    ('2026-09-08T12:00:00Z', '2026-09-08T13:00:00Z'),
    ('2026-09-08T13:00:00Z', '2026-09-08T12:00:00Z'),
])
def test_visual_usage_persistence_preserves_newer_remote_or_stale_cache(tmp_path, local_time, remote_time):
    with authority() as (url, data, _):
        root = tmp_path / 'checkout'
        coordinator = SharedCoordinator(root, ServerBackend(url), owner_id='author')
        with using_coordinator(root, coordinator):
            seed(root, coordinator)
            write_visual_usage(root, 'demo', visual_usage_payload(local_time))
        clone = tmp_path / 'newer_clone'
        newer = SharedCoordinator(clone, ServerBackend(url), owner_id='author')
        with using_coordinator(clone, newer):
            sync_authority(clone, 'demo', download=True)
            write_visual_usage(clone, 'demo', visual_usage_payload(remote_time))
        remote_usage = data['files']['episodes/demo/visual_usage.json']
        remote_artifact = data['files']['.pipeline/artifacts/demo.json']
        with using_coordinator(root, coordinator):
            sync_authority(root, 'demo')  # Refresh the token but preserve the local authored draft.
            with patch.object(visual_usage_persistence, '_git_output', return_value=str(root)), \
                    patch.object(visual_usage_persistence, '_git_result', side_effect=AssertionError('Git forbidden')):
                try:
                    visual_usage_persistence.persist_visual_usage(root, 'demo')
                except (PipelineError, CoordinationError, visual_usage_persistence.VisualUsagePersistenceError):
                    pass
        assert data['files']['episodes/demo/visual_usage.json'] == remote_usage
        assert data['files']['.pipeline/artifacts/demo.json'] == remote_artifact


def test_visual_usage_direct_cas_updates_artifact_hash_and_fences_old_token(tmp_path):
    with authority() as (url, data, _):
        root = tmp_path / 'checkout'
        coordinator = SharedCoordinator(root, ServerBackend(url), owner_id='author')
        with using_coordinator(root, coordinator):
            seed(root, coordinator)
            write_metadata(root, 'demo')  # Establish an authoritative artifact base.
            old_token = coordinator.status('demo')['token']
            target = root / 'episodes/demo/visual_usage.json'
            target.write_text(json.dumps(visual_usage_payload('2026-09-08T13:00:00Z'), indent=2) + '\n', encoding='utf-8')
            with patch.object(visual_usage_persistence, '_git_output', return_value=str(root)), \
                    patch.object(visual_usage_persistence, '_git_result', side_effect=AssertionError('Git forbidden')):
                assert visual_usage_persistence.persist_visual_usage(root, 'demo') is True
            usage = data['files']['episodes/demo/visual_usage.json']
            artifact = json.loads(data['files']['.pipeline/artifacts/demo.json'])
            assert artifact['files']['episodes/demo/visual_usage.json'] == hashlib.sha256(usage).hexdigest()
            with pytest.raises(CoordinationError, match='SHARED_STALE_FENCE'):
                coordinator.commit_mutation(old_token, {'episodes/demo/story.json': b'stale'})
            try:
                write_metadata(root, 'demo')
            except (PipelineError, CoordinationError):
                pass  # Rejecting a cache whose bytes differ from the direct CAS is safe.
            assert data['files']['episodes/demo/visual_usage.json'] == usage
