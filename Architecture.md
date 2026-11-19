"Nunca coloque chamadas de pyserial, pynvml ou imaplib dentro de core/ ou skills/ — use sempre os Adapters."

"Comunicação assíncrona entre módulos deve passar obrigatoriamente pelo EventBus."

"Todas as funções em skills/ devem ter type hints e docstrings claras em português para o Tool Calling do Qwen."

# CONTEXTO DO PROJETO: JARVIS (Assistente Pessoal Local de IA)

Você é o arquiteto de software e desenvolvedor sênior responsável por implementar o projeto **JARVIS** localizado em `C:\Jarvis`. Siga rigorosamente todas as especificações de hardware, arquitetura, padrões de projeto e convenções de nomenclatura descritas abaixo em qualquer geração ou refatoração de código.

---

## 1. Ambiente de Hardware e Restrições Críticas

- **CPU:** AMD Ryzen 5 5600
- **GPU:** NVIDIA GeForce RTX 4060 Ti (8 GB VRAM)
- **RAM:** 32 GB DDR4
- **SO:** Windows (Python 3.12 rodando em ambiente virtual `C:\Jarvis\venv`)
- **Execução SEM Privilégios de Administrador:** O projeto DEVE rodar como usuário comum do Windows para permitir inicialização automática sem bloqueio do UAC e garantir segurança contra _Prompt Injection_.
- **Orçamento de VRAM (8 GB):** O modelo LLM principal (`qwen3.5:9b` via Ollama) deve ser chamado sempre com `options={'num_ctx': 8192}` para caber 100% na VRAM junto com os módulos de voz.
- **Modo Jogo (Proteção de Performance):** Sempre que o uso da RTX 4060 Ti ultrapassar **85%**, tarefas pesadas de segundo plano (como análise automática de e-mails no LLM) devem ser pausadas temporariamente para não competir por VRAM ou causar queda de FPS.

---

## 2. Convenção de Nomenclatura e Estilo de Código

- **Pastas, Arquivos, Variáveis, Funções e Métodos:** Utilize estritamente **camelCase** (ex: `pcHardware`, `jarvisBrain.py`, `getSnapshot()`, `cpuTemp`).
- **Classes:** Utilize **PascalCase** (ex: `EventBus`, `PcStatusFacade`, `SerialConnection`).
- **Idioma:**
  - Nomes de pastas, arquivos, classes, métodos e variáveis: **Inglês (camelCase)**.
  - _Docstrings_ das funções em `skills/` (usadas pelo Tool Calling do Qwen) e respostas/logs para o usuário: **Português claro e objetivo**.
- **Tipagem:** Todo código deve conter _Type Hints_ explícitos do Python.

---

## 3. Árvore Oficial do Projeto (`C:\Jarvis\jarvis-assistant`)

```text

Legenda:
  [OK]     = Módulo implementado e funcional
  [STUB]   = Arquivo criado (0 bytes), aguardando implementação em fase futura
  [TEST]   = Suíte de testes automatizados / simulação mock
  [CONFIG] = Arquivo de configuração / ambiente / documentação
  [IGN]    = Diretório de ambiente ou cache (não versionado)

C:\Jarvis\jarvis-assistant\
├── .env                              [CONFIG] Variáveis locais (COM_PORT, BAUD_RATE, OLLAMA, IMAP)
├── .env.example                      [CONFIG] Template público de variáveis de ambiente
├── .gitattributes                    [CONFIG] Regras de atributos do Git
├── .gitignore                        [CONFIG] Regras de arquivos ignorados pelo Git
├── Architecture.md                   [CONFIG] Regras arquiteturais, convenções e contratos
├── README.md                         [CONFIG] Documentação principal e roadmap do projeto
├── estruturaProjeto.txt              [CONFIG] Este arquivo de documentação estrutural
├── main.py                           [OK]     Ponto de entrada, container DI, lifecycle e loop console
│
├── aiCore\                           [OK]     Núcleo de IA, memória, skills e barramento de eventos
│   ├── __init__.py                   [OK]     Exportações do pacote aiCore
│   ├── eventBus.py                   [OK]     Barramento Pub-Sub assíncrono (Observer Pattern)
│   ├── jarvisBrain.py                [OK]     Orquestrador do Ollama (qwen3.5:9b) e Tool Calling
│   ├── memoryStore.py                [OK]     Janela deslizante de mensagens e cache de IDs de e-mail
│   ├── skillRegistry.py              [OK]     Decorador @registerSkill e registro de ferramentas
│   └── __pycache__\                  [IGN]    Bytecode compilado Python
│
├── appConfig\                        [OK]     Configurações globais e carregamento de .env
│   ├── __init__.py                   [OK]     Exportação do singleton settings
│   ├── settings.py                   [OK]     Singleton Settings (Ollama, Hardware, Arduino, IMAP)
│   └── __pycache__\                  [IGN]    Bytecode compilado Python
│
├── arduinoHub\                       [OK]     Subsistema físico Arduino UNO + LCD 1602A (USB Serial)
│   ├── __init__.py                   [OK]     Exportações públicas do arduinoHub
│   ├── lcdDisplay.py                 [OK]     Observer do EventBus e worker de telas alternadas (16x2)
│   ├── sensorReader.py               [OK]     Leitor daemon de pacotes <SEN:...> / <BTN:...> e <CMD:...>
│   ├── serialConnection.py           [OK]     Singleton thread-safe pyserial com fallback para Mock
│   ├── serialProtocol.py             [OK]     Formatador <LCD:L1|L2> (16 chars) e parser de pacotes
│   └── __pycache__\                  [IGN]    Bytecode compilado Python
│
├── emailService\                     [STUB]   Fase 4 — Triagem inteligente de e-mails (não implementada)
│   ├── __init__.py                   [STUB]   (0 bytes)
│   ├── emailAnalyzer.py              [STUB]   (0 bytes)
│   ├── emailWatcher.py               [STUB]   (0 bytes)
│   └── imapClient.py                 [STUB]   (0 bytes)
│
├── pcHardware\                       [OK]     Telemetria local de CPU, RAM e GPU sem privilégio Admin
│   ├── __init__.py                   [OK]     Exportações do pacote pcHardware
│   ├── cpuRamMonitor.py              [OK]     Leitura de CPU/RAM (psutil) + MAHMSharedMemory (Afterburner)
│   ├── gpuMonitor.py                 [OK]     Leitura da RTX 4060 Ti via pynvml (NVML)
│   ├── pcStatusFacade.py             [OK]     Facade getSnapshot(), Modo Jogo (>85%) e alertas térmicos
│   └── __pycache__\                  [IGN]    Bytecode compilado Python
│
├── skills\                           [STUB]   Fase 2 — Skills dedicadas por domínio (não implementada)
│   ├── __init__.py                   [STUB]   (0 bytes)
│   ├── arduinoSkills.py              [STUB]   (0 bytes)
│   ├── emailSkills.py                [STUB]   (0 bytes)
│   └── pcSkills.py                   [STUB]   (0 bytes)
│
├── tests\                            [TEST]   Testes automatizados
│   ├── testArduinoHub.py             [TEST]   Testes unitários + demo mock do arduinoHub e main.py
│   └── __pycache__\                  [IGN]    Bytecode compilado Python
│
├── voiceEngine\                      [STUB]   Fase 5 — Motor de voz neural (não implementada)
│   ├── __init__.py                   [STUB]   (0 bytes)
│   ├── audioQueue.py                 [STUB]   (0 bytes)
│   ├── voiceInput.py                 [STUB]   (0 bytes)
│   ├── voiceOutput.py                [STUB]   (0 bytes)
│   └── wakeWord.py                   [STUB]   (0 bytes)
│
├── .git\                             [IGN]    Metadados de controle de versão Git
└── venv\                             [IGN]    Ambiente virtual Python 3.12 (Include, Lib, Scripts)




## 4. Padrões de Projeto Obrigatórios (Regras Arquiteturais)
Desacoplamento Total via Observer (aiCore/eventBus.py):

Nenhum módulo de infraestrutura (emailService, pcHardware, arduinoHub) deve importar voiceEngine diretamente.

Quando um evento assíncrono ocorrer (ex: chegou e-mail urgente ou GPU passou de 80°C), o módulo apenas publica no barramento: eventBus.publish("urgentEmailReceived", payload).

O voiceOutput.py e o lcdDisplay.py assinam (subscribe) esses eventos para falar no alto-falante e mostrar no LCD.

Facade de Telemetria (pcHardware/pcStatusFacade.py):

Centraliza as chamadas de gpuMonitor.py e cpuRamMonitor.py.

A leitura da temperatura do Ryzen 5 5600 em cpuRamMonitor.py deve ler a memória compartilhada do MSI Afterburner (MAHMSharedMemory via módulos nativos mmap e struct, buscando "CPU temperature" ou "Temperatura da CPU"), funcionando sem privilégio de Admin.

A leitura da RTX 4060 Ti em gpuMonitor.py usa pynvml.

Singleton Thread-Safe na Porta Serial (arduinoHub/serialConnection.py):

O Arduino UNO reseta ao abrir a conexão serial e não permite múltiplos acessos simultâneos à mesma porta COM.

SerialConnection deve manter uma única instância aberta e protegida por threading.Lock() para que o worker de atualização contínua do LCD e os comandos de voz da IA não colidam.

Protocolo Serial Padronizado (arduinoHub/serialProtocol.py):

Toda comunicação via cabo USB deve usar marcadores de início e fim:

Envio para tela LCD: <LCD:L1_TEXTO|L2_TEXTO>\n

Comandos de atuadores: <CMD:ACAO:VALOR>\n

Leitura de sensores vindos do Arduino: <SEN:NOME:VALOR>\n

Auto-Registro de Ferramentas da IA (aiCore/skillRegistry.py):

Crie o decorador @registerSkill. Qualquer função decorada dentro da pasta skills/ deve ser automaticamente adicionada à lista de tools enviada para ollama.chat(model='qwen3.5:9b', ...) e ao dicionário de execução do jarvisBrain.py.

Prevenção de Loop de Áudio (voiceEngine/audioQueue.py):

Todas as saídas de voz passam por uma queue.PriorityQueue.

Enquanto voiceOutput.py estiver reproduzindo áudio no alto-falante, um estado/evento isSpeaking deve pausar a captura do voiceInput.py para evitar que o JARVIS escute a própria voz.




## 5. Como Responder às Solicitações de Implementação
Sempre verifique em qual arquivo da árvore camelCase o código pertence antes de criá-lo ou editá-lo.

Não crie arquivos fora da estrutura definida sem antes perguntar.

Implemente tratamento de erros resiliente (try/except): se o Arduino estiver desconectado da USB ou o MSI Afterburner estiver fechado, o sistema deve continuar funcionando normalmente e avisar nos logs sem derrubar o assistente.
```
