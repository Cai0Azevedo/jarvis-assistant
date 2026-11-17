# 🤖 JARVIS Assistant (`jarvis-assistant`)

<p align="center">
  <img src="https://img.shields.io/badge/Python-3.12-3776AB?style=for-the-badge&logo=python&logoColor=white" alt="Python 3.12" />
  <img src="https://img.shields.io/badge/Ollama-Local_LLM-000000?style=for-the-badge&logo=ollama&logoColor=white" alt="Ollama" />
  <img src="https://img.shields.io/badge/Model-Qwen_3.5_(9B)-615CED?style=for-the-badge" alt="Qwen 3.5 9B" />
  <img src="https://img.shields.io/badge/NVIDIA-RTX_4060_Ti_(8GB)-76B900?style=for-the-badge&logo=nvidia&logoColor=white" alt="RTX Powered" />
  <img src="https://img.shields.io/badge/OS-Windows_10%2F11-0078D6?style=for-the-badge&logo=windows&logoColor=white" alt="Windows" />
  <img src="https://img.shields.io/badge/License-MIT-F59E0B?style=for-the-badge" alt="License MIT" />
</p>

> **Assistente pessoal inteligente 100% local**, projetado com **Clean Architecture + Event-Driven Design** para oferecer custo zero de API, privacidade absoluta, baixa latência, integração física com microcontrolador (**Arduino UNO + LCD I2C**) e telemetria avançada de hardware no Windows **sem exigir privilégios de Administrador**.

---

## ✨ Visão Geral e Diferenciais Técnicos

O **Projeto JARVIS** resolve os principais gargalos de assistentes baseados em nuvem (latência de rede, custos recorrentes de tokens e exposição de dados sensíveis) executando toda a pilha de IA, voz e automação localmente em hardware doméstico:

- 🔒 **100% Local, Privado e Custo Zero:** Orquestração local via **Ollama** utilizando o modelo **`qwen3.5:9b`** com suporte nativo a _Tool Calling_, transcrição neural com **Faster-Whisper** e síntese de voz neural com **Piper TTS**.
- 🛡️ **Execução Sem Privilégios de Administrador (Zero-Admin):** Todo o sistema roda como usuário padrão do Windows, permitindo inicialização automática no boot sem bloqueios do **UAC (User Account Control)** e reduzindo drasticamente a superfície de ataque contra _Prompt Injection_.
- 📊 **Telemetria de Hardware via Memória Compartilhada:** Monitora uso, temperatura e VRAM da **RTX 4060 Ti** via `pynvml` e extrai a temperatura do **AMD Ryzen 5 5600** diretamente da memória compartilhada do **MSI Afterburner (`MAHMSharedMemory`)** usando `mmap` e `struct` nativos — sem precisar de drivers _ring-0_ em modo Admin.
- 🎮 **Modo Jogo Inteligente (Proteção de FPS e VRAM):** Sempre que o uso da GPU ultrapassa **85%**, tarefas assíncronas pesadas de segundo plano (como triagem automática de e-mails no LLM) são pausadas automaticamente para evitar disputa por VRAM ou quedas de _framerate_.
- 🔌 **Integração Física com Microcontrolador:** Comunicação serial USB thread-safe com **Arduino UNO** para exibição contínua de telemetria e alertas em tela **LCD I2C**, além de leitura de sensores e acionamento de atuadores/relés.
- 🧩 **Arquitetura Resiliente e Desacoplada:** Se o Arduino estiver desconectado da USB ou o MSI Afterburner estiver fechado, o sistema degrada graciosamente, registra avisos nos logs e continua operando sem interrupções.

---

## 🖥️ Especificações de Hardware e Orçamento de VRAM (8 GB)

O projeto foi calibrado especificamente para caber com folga nos **8 GB de VRAM** da **RTX 4060 Ti**, mantendo o modelo principal e o pipeline de áudio carregados sem _swapping_ para a RAM do sistema.

| Componente / Recurso         | Especificação de Referência            | Papel no Sistema / Restrição Crítica                                |
| :--------------------------- | :------------------------------------- | :------------------------------------------------------------------ |
| **Processador (CPU)**        | AMD Ryzen 5 5600 (6C/12T)              | Orquestração assíncrona, threads de I/O e pré-processamento         |
| **Placa de Vídeo (GPU)**     | NVIDIA GeForce RTX 4060 Ti (8 GB VRAM) | Inferência local do LLM (`qwen3.5:9b`) e aceleração CUDA            |
| **Memória do Sistema (RAM)** | 32 GB DDR4                             | Histórico em memória, buffers de áudio e processos do Windows       |
| **Sistema Operacional**      | Windows 10 / 11 (64-bit)               | Execução em espaço de usuário comum (sem elevação UAC)              |
| **LLM Principal (Ollama)**   | `qwen3.5:9b` (Quantização Q4_K_M)      | Chamado estritamente com `options={'num_ctx': 8192}` (~6.2 GB VRAM) |
| **STT / TTS**                | Faster-Whisper + Piper TTS             | Reserva otimizada de VRAM/CPU para baixa latência de voz            |
| **Proteção de Performance**  | Limiar de **85% de uso da GPU**        | Ativa o **Modo Jogo**, pausando análises automáticas em background  |

---

## 🏗️ Arquitetura de Software

O JARVIS combina princípios de **Clean Architecture** com uma arquitetura **Orientada a Eventos (Event-Driven)**. Para detalhes completos das regras arquiteturais e contratos de módulos, consulte o documento oficial [**`ARCHITECTURE.md`**](./Architecture.md) na raiz do repositório.

### Padrões de Projeto Aplicados

1. **Observer Pattern (`aiCore/eventBus.py`):** Barramento Pub-Sub assíncrono (`EventBus`). Nenhum módulo de infraestrutura (`pcHardware`, `emailService`, `arduinoHub`) importa `voiceEngine` diretamente; eventos como `urgentEmailReceived`, `gpuHighTempAlert` ou `gamingModeChanged` são publicados no barramento e consumidos pelos assinantes.
2. **Facade Pattern (`pcHardware/pcStatusFacade.py`):** Centraliza os _Adapters_ `GpuMonitor` (`pynvml`) e `CpuRamMonitor` (`psutil` + `MAHMSharedMemory`) em uma interface única `getSnapshot()`.
3. **Command / Registry Pattern (`aiCore/skillRegistry.py`):** O decorador `@registerSkill` registra automaticamente funções Python tipadas (com _docstrings_ em português) tanto na lista `tools` enviada ao `ollama.chat()` quanto no dicionário de execução do `JarvisBrain`.
4. **Thread-Safe Singleton (`appConfig/settings.py` e `arduinoHub/serialConnection.py`):** Garante instância única para as configurações globais e protege a porta COM do Arduino UNO com `threading.Lock()`, evitando resets acidentais da placa ou colisões entre a thread do LCD e comandos de voz.

---

## 📂 Estrutura de Diretórios (`camelCase`)

Todo o código-fonte segue rigorosamente a convenção **camelCase** para pastas, arquivos, variáveis, funções e métodos, e **PascalCase** para classes:

```text
C:\Jarvis\
├── appConfig\
│   ├── __init__.py
│   └── settings.py              # Singleton: carrega .env, porta COM, configs do Ollama e intervalos
├── aiCore\
│   ├── __init__.py
│   ├── jarvisBrain.py           # Orquestrador do Ollama (qwen3.5:9b) e loop de Tool Calling
│   ├── skillRegistry.py         # Decorator @registerSkill (Command / Registry Pattern)
│   ├── eventBus.py              # Barramento Pub-Sub assíncrono (Observer Pattern)
│   └── memoryStore.py           # Histórico de conversa (janela deslizante) e cache de IDs de e-mails lidos
├── voiceEngine\
│   ├── __init__.py
│   ├── wakeWord.py              # Detecção contínua da palavra de ativação ("Jarvis")
│   ├── voiceInput.py            # Captura de microfone e transcrição (Faster-Whisper)
│   ├── voiceOutput.py           # Síntese de voz no alto-falante (Piper TTS)
│   └── audioQueue.py            # Fila de prioridade de fala + mute do microfone enquanto a IA fala
├── pcHardware\
│   ├── __init__.py
│   ├── gpuMonitor.py            # Adapter NVIDIA (pynvml / nvidia-ml-py): uso, VRAM e temp da RTX 4060 Ti
│   ├── cpuRamMonitor.py         # Adapter CPU/RAM: psutil + memória compartilhada do MSI Afterburner
│   └── pcStatusFacade.py        # Facade: unifica dados de CPU, GPU e RAM em um único método getSnapshot()
├── arduinoHub\
│   ├── __init__.py
│   ├── serialConnection.py      # Singleton com Thread Lock para porta USB (pyserial) do Arduino UNO
│   ├── serialProtocol.py        # Empacotamento de mensagens seriais (<LCD:...>, <CMD:...>, <SEN:...>)
│   ├── lcdDisplay.py            # Envia telemetria do PC e alertas de eventos para a tela LCD do Arduino
│   └── sensorReader.py          # Leitura de sensores externos e controle de atuadores/relés futuros
├── emailService\
│   ├── __init__.py
│   ├── imapClient.py            # Adapter IMAP para buscar e-mails não lidos
│   ├── emailAnalyzer.py         # Envia assunto/remetente/corpo ao Qwen 3.5 para classificar urgência e resumir
│   └── emailWatcher.py          # Worker em thread paralela que checa novos e-mails periodicamente
├── skills\
│   ├── __init__.py
│   ├── pcSkills.py              # Skills expostas à IA para consultar desempenho e temperaturas do PC
│   ├── arduinoSkills.py         # Skills expostas à IA para enviar mensagens ao LCD ou acionar sensores/relés
│   └── emailSkills.py           # Skills expostas à IA para resumir ou buscar e-mails importantes sob demanda
├── Architecture.md              # Especificação arquitetural completa do projeto
├── main.py                      # Ponto de entrada: inicializa EventBus, registra skills, sobe threads e loop
├── .env.example                 # Template público de variáveis de ambiente
└── .gitignore                   # Regras de exclusão do Git
```

---

## 🚀 Guia Passo a Passo de Instalação e Configuração

### 1. Clonagem e Criação do Ambiente Virtual (Python 3.12)

Abra o **PowerShell** (como usuário comum, sem precisar de Administrador):

```powershell
git clone https://github.com/seu-usuario/jarvis-assistant.git C:\Jarvis
cd C:\Jarvis

# Cria o ambiente virtual com Python 3.12
py -3.12 -m venv venv

# Ativa o ambiente virtual
.\venv\Scripts\Activate.ps1
```

### 2. Instalação das Dependências

Com o ambiente virtual ativo, atualize o `pip` e instale as dependências do projeto:

```powershell
python -m pip install --upgrade pip
pip install -r requirements.txt
```

> 💡 _Caso ainda não possua o `requirements.txt` gerado no ambiente local, os pacotes base da Fase 1 são: `pip install ollama psutil nvidia-ml-py pyserial`._

### 3. Configuração do Ollama e Download do Modelo

1. Baixe e instale o [Ollama para Windows](https://ollama.com/download).
2. Baixe e teste o modelo **`qwen3.5:9b`**:

```powershell
ollama run qwen3.5:9b
```

_(Após verificar que o modelo respondeu no terminal, digite `/bye` para sair do prompt interativo do Ollama)._

### 4. Configuração do MSI Afterburner e Arduino UNO

- **MSI Afterburner (Temperatura do Ryzen 5 5600 sem Admin):**
  1. Abra o **MSI Afterburner** -> **Configurações (ícone de engrenagem)** -> aba **Monitoramento**.
  2. Certifique-se de que o sensor **`CPU temperature`** (ou **`Temperatura da CPU`**) está marcado como ativo na lista de gráficos.
  3. Mantenha o MSI Afterburner aberto (minimizado na bandeja do sistema) para expor o bloco `MAHMSharedMemory`.
- **Arduino UNO:**
  1. Conecte o Arduino UNO via cabo USB.
  2. Verifique no **Gerenciador de Dispositivos do Windows** (em _Portas COM e LPT_) qual porta foi atribuída (ex: `COM3`).

### 5. Configuração das Variáveis de Ambiente (`.env`)

Copie o arquivo de template `.env.example` para `.env` e preencha suas configurações locais:

```powershell
Copy-Item .env.example .env
notepad .env
```

Ajuste especialmente as variáveis `COM_PORT`, `EMAIL_USER` e `EMAIL_PASSWORD` (utilize uma _Senha de App_ caso use Gmail).

### 6. Execução do Assistente

Inicie o JARVIS a partir da raiz do projeto:

```powershell
python main.py
```

Comandos rápidos disponíveis no modo console:

- `/status` ou `/hw`: Exibe instantaneamente a telemetria de CPU, RAM, GPU, VRAM e estado do Modo Jogo.
- `/limpar`: Reinicia a janela deslizante de memória da conversa atual.
- `/sair`: Encerra todas as threads e conexões de forma segura.

---

## 🔌 Protocolo Serial USB e Proteção de Áudio

### Protocolo Serial Determinístico (`arduinoHub/serialProtocol.py`)

Toda comunicação via porta USB entre o PC e o Arduino UNO é protegida por um `threading.Lock()` no Singleton `SerialConnection` e utiliza delimitadores explícitos de início (`<`) e fim (`>\n`):

| Direção          | Formato do Pacote          | Exemplo                              | Descrição                                          |
| :--------------- | :------------------------- | :----------------------------------- | :------------------------------------------------- |
| **PC → Arduino** | `<LCD:LINHA_1\|LINHA_2>\n` | `<LCD:CPU: 42% 58C\|GPU: 35% 49C>\n` | Atualiza as duas linhas do display LCD I2C         |
| **PC → Arduino** | `<CMD:ACAO:VALOR>\n`       | `<CMD:RELAY1:ON>\n`                  | Aciona relés, LEDs ou atuadores conectados         |
| **Arduino → PC** | `<SEN:NOME:VALOR>\n`       | `<SEN:TEMP_AMB:24.5>\n`              | Reporta leitura de sensores externos ao `EventBus` |

### Prevenção de Loop de Áudio / Eco (`voiceEngine/audioQueue.py`)

Para impedir que o microfone capture a própria voz sintetizada pelo assistente:

1. Toda requisição de fala entra em uma fila de prioridade (`queue.PriorityQueue`) em `audioQueue.py`.
2. Enquanto o `voiceOutput.py` (Piper TTS) está reproduzindo áudio no alto-falante, o estado `isSpeaking` permanece ativo.
3. O módulo `voiceInput.py` (Faster-Whisper) monitora `isSpeaking` e pausa automaticamente a captura do microfone até o término da reprodução.

---

## 🗺️ Status do Roadmap

- [x] **Fase 1 — Fundação, Telemetria e Core de IA (`appConfig`, `pcHardware`, `aiCore`, `main.py` em modo console):**
  - Singleton `Settings` com carregamento nativo de `.env`.
  - Adapters `GpuMonitor` (`pynvml`) e `CpuRamMonitor` (`psutil` + `MAHMSharedMemory`).
  - `PcStatusFacade` com detecção de Modo Jogo (>85% GPU) e alertas térmicos.
  - `EventBus` assíncrono, `MemoryStore` com janela deslizante, `@registerSkill` e `JarvisBrain` (`qwen3.5:9b` com `num_ctx: 8192`).
- [ ] **Fase 2 — Camada de Skills Dedicadas (`skills/`):**
  - Implementação completa de `pcSkills.py`, `arduinoSkills.py` e `emailSkills.py` integradas ao Tool Calling.
- [ ] **Fase 3 — Hub Físico Arduino UNO (`arduinoHub/`):**
  - Implementação de `serialConnection.py`, `serialProtocol.py`, `lcdDisplay.py` e `sensorReader.py`.
- [ ] **Fase 4 — Triagem Inteligente de E-mails (`emailService/`):**
  - Implementação de `imapClient.py`, `emailAnalyzer.py` e worker `emailWatcher.py` com respeito ao Modo Jogo.
- [ ] **Fase 5 — Motor de Voz Neural (`voiceEngine/`):**
  - Integração de Wake Word ("Jarvis"), Faster-Whisper (`voiceInput.py`), Piper TTS (`voiceOutput.py`) e controle anti-eco (`audioQueue.py`).

---

## 📄 Licença

Distribuído sob a licença **MIT**. Consulte os termos padrão da licença MIT para uso, modificação e distribuição.
