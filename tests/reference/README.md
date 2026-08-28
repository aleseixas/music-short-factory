# Referência visual aprovada

`through_the_wire_v7.mp4` é o vídeo aprovado anterior à refatoração.

- SHA-256: `C46DCC3DAF7F2C2E9AA6C582AB45FF9197E471FD0346A31BA4971D34FC1F642D`
- resolução: 720x1280 (9:16)
- frame rate: 30 fps
- frames: 1119
- duração do vídeo: 37,30 s
- duração da narração: 37,27 s

O diretório `output/` é descartável; esta cópia explícita não é um arquivo
temporário e deve ser preservada para regressões futuras.

## Resultado da refatoração

O render executado com `python generate.py through_the_wire` foi validado com:

- 1119 frames, 37,30 s, 720x1280 e 30 fps;
- stream de áudio SHA-256 idêntico ao aprovado;
- SSIM quadro a quadro `0.990655` (`Y=0.988927`, `U=0.994052`,
  `V=0.994172`);
- mesma timeline, mesmos crops, captions e highlights.

O SSIM não é 1 porque os intermediários móveis da referência histórica não
eram bit a bit reproduzíveis a partir do estado encontrado, mas a inspeção
lado a lado confirmou o mesmo enquadramento, estilo e qualidade percebida.
