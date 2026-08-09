# SteamOS LED → WLED Bridge

Replica a barra de LEDs do Steam Deck para um controlador [WLED](https://github.com/Aircoookie/WLED) via protocolo UDP realtime (DRGB).

Sem firmware customizado — basta um dispositivo WLED de fábrica na sua rede.

**[Read in English](README.md)**

## Como funciona

1. Lê snapshots de 100 bytes do `/dev/valve-leds-shim` (módulo kernel)
2. Remapeia 17 LEDs de origem para o comprimento da sua fita
3. Renderiza efeitos do Steam Deck no servidor (rainbow, breath, patrol, factory, demo)
4. Aplica overlays opcionais (temperatura, notificações, VU de áudio)
5. Envia dados de pixel via **UDP DRGB** (porta 21324) para o WLED — mesmo protocolo do HyperHDR
6. Toca animações de boot/shutdown/suspend/resume

## Requisitos

- **Steam Deck** (SteamOS) ou PC Linux rodando Steam em Game Mode
- **Dispositivo WLED** na mesma rede (ESP8266, ESP32, etc.)
- **Python 3** (sem pacotes extras — usa apenas stdlib)

### Configurando o WLED (pré-requisito)

Você precisa de um controlador WLED na sua rede antes de instalar esta bridge. Passos rápidos:

1. **Instale o WLED** em uma placa ESP8266 ou ESP32:
   - Baixe a última versão em [WLED releases](https://github.com/Aircoookie/WLED/releases)
   - Use o [instalador web WLED](https://install.wled.me/) (Chrome/Edge) para a forma mais fácil
2. **Conecte ao WiFi**: Após instalar, conecte ao hotspot `WLED-AP` e configure suas credenciais WiFi
3. **Configure a fita LED**: Em configurações do WLED → LED Preferences, defina a quantidade de LEDs e tipo (WS2812B, SK6812, etc.)
4. **Anote o endereço IP**: O WLED mostra o IP na página principal. O instalador também pode descobrir automaticamente
5. **Habilite sync UDP**: Em configurações do WLED → Sync Interfaces, certifique-se de que a porta UDP é **21324** (padrão)

Para instruções detalhadas, veja a [documentação do WLED](https://kno.wled.ge/).

## Instalação

```bash
git clone https://github.com/rpf16rj/steamos-led-wled.git
cd steamos-led-wled
sudo ./install.sh
```

O instalador vai:
1. Tentar **descobrir automaticamente** dispositivos WLED na sua rede (mDNS / scan de subnet)
2. Caso não encontre, perguntar o IP manualmente
3. Perguntar quantidade de LEDs e preferências de overlay
4. Compilar e instalar o módulo kernel `leds-valve-shim`
5. Instalar o serviço bridge e gerar `/etc/steamos-led-wled.conf`
6. Habilitar e iniciar o serviço systemd

## Plugin Decky Loader

Controle os overlays diretamente do Game Mode usando o plugin Decky **Toolkit SteamOS Control**:

- Repositório: [toolkit-steamos-control-decky](https://github.com/rpf16rj/toolkit-steamos-control-decky)
- Ative/desative overlays de temperatura, VU de áudio e notificações sem sair do Game Mode

## Configuração

Edite `/etc/steamos-led-wled.conf`:

```ini
[steamos-led-wled]
# Endereço IP do dispositivo WLED
wled_host = 192.168.1.100

# Porta UDP realtime do WLED (padrão: 21324)
wled_port = 21324

# Número de LEDs na sua fita (1-17)
num_leds = 8

# Caminho para o dispositivo valve-leds-shim
device = /dev/valve-leds-shim

# Recursos de overlay (true/false)
temp_overlay = true
notify_overlay = true
audio_overlay = true
```

Após editar, reinicie o serviço:

```bash
sudo systemctl restart steamos-led-wled
```

## Recursos

### Efeitos de LED (renderização no servidor)

Todos os efeitos de LED do Steam Deck são renderizados pela bridge e enviados ao WLED:

| Efeito | Descrição |
|--------|-----------|
| **Manual** | Cor estática definida pelo Game Mode |
| **Rainbow** | Matizes ciclando por todos os LEDs |
| **Breath** | Brilho pulsante |
| **Patrol** | Luz ping-pong |
| **Factory** | Cores complementares alternadas |
| **Demo** | Cicla por todos os efeitos |

### Recursos de overlay

| Overlay | Descrição |
|---------|-----------|
| **Temperatura** | Colore a barra amarelo→vermelho baseado na temp CPU/GPU (> 65°C) |
| **Notificações** | Pisca dourado para conquistas, azul para mensagens |
| **VU de Áudio** | Medidor VU pelo áudio do sistema (PipeWire/PulseAudio) |

Prioridade: Notificação > Áudio+Temperatura > Game Mode

### Animações

| Evento | Animação |
|--------|----------|
| **Boot** | Sweep do centro para fora em azul Steam, depois pulso |
| **Shutdown** | Fade das bordas para o centro |
| **Suspend** | Fade lento para preto |
| **Resume** | Sweep rápido do centro para fora |

## Comandos

```bash
# Ver status
sudo systemctl status steamos-led-wled

# Ver logs
sudo journalctl -u steamos-led-wled -f

# Reiniciar
sudo systemctl restart steamos-led-wled

# Parar
sudo systemctl stop steamos-led-wled
```

## Desinstalar

```bash
sudo ./uninstall.sh
```

## Protocolo UDP

Usa o protocolo realtime **DRGB** do WLED na porta 21324:

| Byte | Valor | Descrição |
|------|-------|-----------|
| 0 | `0x02` | Protocolo: DRGB |
| 1 | `0x02` | Timeout: 2 segundos |
| 2+n×3 | R | Valor vermelho para LED n |
| 3+n×3 | G | Valor verde para LED n |
| 4+n×3 | B | Valor azul para LED n |

Este é o mesmo protocolo usado pelo HyperHDR, Hyperion e outras soluções ambilight.

## Licença

MIT
