# Changelog

Todas as mudanças notáveis neste projeto serão documentadas neste arquivo.

## [1.0.0] - 2026-08-09

### Adicionado

- Lançamento inicial
- Bridge UDP realtime para WLED usando protocolo DRGB (porta 21324)
- Lê snapshots de LED do `/dev/valve-leds-shim` (módulo kernel valve-leds-shim)
- Remapeia 17 LEDs de origem para comprimento configurável da fita (1-17)
- Renderização no servidor dos efeitos de LED do Steam Deck:
  - Manual (cor estática)
  - Rainbow (matizes ciclando por LED)
  - Breath (brilho pulsante)
  - Patrol (luz ping-pong)
  - Factory (cores complementares alternadas)
  - Demo (cicla por todos os efeitos)
- Recursos de overlay:
  - Temperatura: barra de cor amarelo→vermelho baseada na temp CPU/GPU
  - Notificações: flash em conquistas/mensagens do Steam via DBus
  - Áudio reativo: medidor VU pelo PipeWire/PulseAudio
- Animações:
  - Boot: sweep do centro para fora em azul Steam
  - Shutdown: fade das bordas para o centro
  - Suspend: fade lento para preto
  - Resume: sweep rápido do centro para fora
- Descoberta automática de dispositivos WLED (mDNS + scan de subnet)
- Configuração via `/etc/steamos-led-wled.conf`
- Instalador interativo com gerenciamento de dependências (SteamOS, Arch, Debian, Fedora)
- Serviço systemd com auto-restart
- Script de desinstalação
- Integração com plugin Decky Loader (Toolkit SteamOS Control)
