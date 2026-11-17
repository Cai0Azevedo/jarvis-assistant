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
C:\Jarvis\jarvis-assistant
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
│   ├── gpuMonitor.py            # Adapter NVIDIA (pynvml / nvidia-ml-py): uso, VRAM e temperatura da RTX 4060 Ti
│   ├── cpuRamMonitor.py         # Adapter CPU/RAM: psutil + leitura da memória compartilhada do MSI Afterburner
│   └── pcStatusFacade.py        # Facade: unifica dados de CPU, GPU e RAM em um único método getSnapshot()
├── arduinoHub\
│   ├── __init__.py
│   ├── serialConnection.py      # Singleton com Thread Lock para porta USB (pyserial) do Arduino UNO
│   ├── serialProtocol.py        # Empacotamento de mensagens seriais (ex: <LCD:...> e <CMD:...>)
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
├── main.py                      # Ponto de entrada: inicializa EventBus, registra skills, sobe threads e inicia o loop
└── .env                         # Variáveis sensíveis (credenciais IMAP, COM_PORT, modelo LLM)





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
