<img src="web/logo.svg" alt="SpinMate logo" width="96">

# SpinMate

SpinMate is a web interface for a two-sublattice macrospin simulator (antiferromagnets, FeRh and
exchange-coupled ferromagnets). You describe a run in plain language, or attach a parameter file, and
The selected Claude or OpenAI model checks the input with the simulator's own parser, starts the run, follows it and explains the
result from the actual magnetization curves.

<img width="1911" height="952" alt="Screenshot 2026-10-09 160300" src="https://github.com/user-attachments/assets/cff4aabd-e6aa-49bd-8851-103502936f08" />




The physics code in `macrospin/` is the original code and has not been modified. The repository is
named `sublattice-agent`.

## Contents

- [Quick start](#quick-start)
- [Using the app](#using-the-app)
- [Simple scenarios to try](#simple-scenarios-to-try)
- [Parameter files](#parameter-files)
- [Behaviour of the original code](#behaviour-of-the-original-code)
- [Configuration](#configuration)
- [Behind a corporate proxy](#behind-a-corporate-proxy)
- [How it works](#how-it-works)
- [Development](#development)

## Quick start

You need Docker and at least one API key: Anthropic (console.anthropic.com) or OpenAI (platform.openai.com).

```bash
cp .env.example .env          # put a key in ANTHROPIC_API_KEY and/or OPENAI_API_KEY
docker compose up --build
```

Open http://localhost:8421. The host port can be changed with `HOST_PORT` in `.env`.

If you leave `ANTHROPIC_API_KEY` empty you can paste the key in the settings of the page instead. It is
kept in that browser tab only and sent to your own server with each message.

Results are stored in the `sublattice-data` Docker volume. To keep them in a folder on disk, replace the
volume line in `docker-compose.yml` with `- ./workspace:/data`.

Without Docker:

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
export ANTHROPIC_API_KEY=sk-ant-...
uvicorn app.server:app --port 8000
```

## Using the app

**Chat.** The centre of the page is the chat. Type what you want to run, or attach a `.txt` parameter file
with the paperclip (drag and drop also works). Under the message box you choose the model and the mode:

- **Fast** uses the default parameters, changes only what you name and gives a short answer.
- **Deep** works from the full parameter set or your txt file, mentions the caveats that apply and gives a
  quantitative explanation.

Under the message box you choose one of four gods: **Hermes** (quick and economical), **Dionysus** (balanced),
**Zeus** (deep and most capable) or **Apollo** (specialised and new models). The model behind each god is chosen
automatically from the keys you have: for every provider it takes the newest suitable model of that level (for
Hermes, for example, Claude Haiku or an OpenAI nano/mini model), tries Claude first and switches to OpenAI if a
call fails, for instance when there is no credit. Each answer shows which model was used. To always use a given
model for a god, open Settings and pick it under *Model behind each god*; the default is Auto. The logos are
read from `web/gods/` (`hermes.png`, `dionysus.png`, `zeus.png`, `apollo.png`); until a file exists the god
shows a coloured badge with its initial.
The temple icon in the left bar opens *Model gods*, a comparison of the four levels with what each is good
for, its speed and depth, and the model each one will use, plus a button to choose it.

**Agent behaviour.** After it starts a run the assistant waits for it, reads the result (including the
plot) and reports it, so a single request such as "run the default" ends with an explanation. For goal
requests ("find the smallest current that switches the Néel vector") it searches on its own: a coarse
sweep, then finer sweeps inside the interval where the behaviour changes, for at most four rounds, and it
reports the interval it found. The assistant is the only part that uses tokens; runs started from Run setup
never do.

**Tokens.** A counter next to the Fast and Deep switch shows the tokens used in the current chat, and each
answer ends with its own input and output tokens. Set `TOKEN_BUDGET` to stop the assistant, and with it any
run it would start, once the server has used that many tokens since it started.

**Conversations.** The clock icon in the left bar lists earlier chats with their runs and graphs; the
pencil starts a new one. Everything appears in the chat under the prompt that asked for it; there is no side panel.

**Default parameters.** The sliders icon in the left bar edits the values Fast mode starts from. They are
stored in the browser and also sent to the assistant as the base of its runs.

**Export.** Each run card has *Export CSV* (time, m1, m2 and the Néel vector) and *Export all data*
(a zip with every output file and the CSV).

**Runs in the chat.** Every run adds a card that shows its steps (parameters checked, run started,
simulating with progress and time left) and, when it finishes, the complete viewer, so the result is
there as soon as the answer is: a dial with the +x and +z axes showing the sublattice orientation, the
order before and after (for example AFM to FM), the final vectors, and a plot of m1, m2 and the Néel
vector. Toggle components, click to move the playhead, drag to zoom (the window is read again from the raw
data), double-click to reset, and press Play to animate the run. Sweep cards plot mean |nz| against the
swept parameter; click a point to open that run.

**Run setup.** The play icon in the left bar opens a dialog for starting a run yourself, without the
assistant. The result appears in the chat like any other.

- *Fast*: presets, sliders and number fields for duration, Ms, exchange A0, anisotropy Ku, damping,
  temperature and SOT current, an initial-state selector and a "Quick extra txt" field for one or two
  additional flags. The text that will be run is shown below the form.
- *Deep*: a text editor for the full parameter file, an examples menu, Check, Run and Save as file.

The app works without the assistant: Run setup runs a simulation directly and only the chat needs the API.

**Athena.** The owl in the bottom right corner (named after the Greek goddess of wisdom) is a small
assistant that only explains how to use the app. It asks for your name the first time and answers questions such as where the results are or what
Fast and Deep do. If the selected provider cannot be reached it falls back to a built-in list of answers.

**Account.** There is no password: you log in by typing your name when Athena asks for it. The round button
with your initial in the left bar shows who is signed in and lets you log out. Every name keeps its own
chat history and default parameters in the browser.

**Settings** (gear in the left bar): colour theme (Light, Dark, Spin), Anthropic API key and OpenAI API key.

By default a run is deterministic: temperature 0 and no current. See the note on `SOT_DC_Amp` below.

## Simple scenarios to try

All of these use `--t=2e-9`, no current and T = 0. The default initial state is antiferromagnetic
(m1 close to -m2, Néel vector along -z). Each one was run with the real solver.

| Scenario | Parameters | Result |
|---|---|---|
| AFM to FM (default) | `--A0=0.248e-12` | m1·m2 goes from -1 to +1 in about 50 ps |
| AFM to AFM | `--A0=-0.248e-12` | stays antiparallel |
| FM to AFM | `--A0=-0.248e-12 --m1=0.25,0,0.968 --m2=0.35,0,0.937` | becomes antiparallel in about 20 ps |
| FM to FM | `--A0=0.248e-12 --m1=0.25,0,0.968 --m2=0.35,0,0.937` | stays parallel |
| FeRh heating | `--A0=0.248e-12 --flagTempVarying --Temp=60 --Fr=0.25e9` | AFM below about 375 K, FM above |

The first four are not phase transitions in the thermodynamic sense: the exchange sign decides which
alignment is the ground state, and the start state relaxes towards it. The FeRh case is driven by
temperature through the model's own switching point.

The `examples/` folder has a file for each scenario, plus a few driven cases:

| File | Content |
|---|---|
| `afm_to_fm_transition.txt`, `afm_stable_default.txt` | AFM to FM, AFM to AFM |
| `fm_to_afm.txt`, `fm_to_fm.txt` | FM starts |
| `ferh_heating_afm_to_fm.txt`, `ferh_temperature_cycle.txt` | FeRh heating, FeRh temperature cycle |
| `neel_minus_z_no_current.txt`, `neel_plus_z_no_current.txt` | Néel vector starting along -z or +z |
| `sot_switching.txt`, `ac_drive_25GHz.txt`, `chirp_pulse.txt` | driven dynamics |

## Continuing a run

Ask "run 0-2 ns, then continue 2 ns from the last state and show me one plot", or press **Continue run**
on a finished card. `continue_run` starts a new run from the final m1, m2 (full precision) and keeps the
clock running: the solver gets `t0 + t_offset`, so AC fields, A0(t), Ku(t), the chirp and the FeRh
temperature cycle do not restart their phase. **Combine chain** (`combine_runs`) joins the segments into
one run with one trajectory, plot, CSV and summary; segment joins are dotted lines on the plot.
A FeRh run of 0-2 ns + 2-4 ns matches a single 4 ns run to about 1e-14. With thermal noise (`flag0`)
the random sequence differs from one long run, and `--gaussian` values are drawn again per segment.

## Parameter files

The format is that of `main.py --input-file`: command-line options, one or several per line. Lines starting
with `#` are comments.

```text
# 5 ns of SOT-driven dynamics
--t=5e-9
--SOT_DC_Amp=20e6
--SOT_pol=0,1,0
--flag0 --Temp=300
```

The full list of parameters with units is in `app/prompts.py`. A sweep ("sweep Fr from 10 to 40 GHz in
5 GHz steps") starts one run per value, as the original `prepare.py`, `run_sim.py` and `avg_neel.py` do.

Each run has its own folder with the original outputs (`output1.dat`, `output2.dat`,
`Two_Spin_Dynamics.png`, logs) and two additions: `neel_z.dat` and `summary.json`. The summary holds the
final m1, m2 and Néel vector, mean |nz|, the number of nz sign changes, the dominant oscillation
frequency and the order (AFM, FM or canted) at the start and at the end.

## Behaviour of the original code

The assistant knows about these and mentions them when they matter.

- `flag5` (SOT) is always on, and the solver's default current is 30e6 A/cm². This app applies
  `--SOT_DC_Amp=0` unless the file sets a current, so that default runs have none.
- A positive `A0` is ferromagnetic coupling and a negative one is antiferromagnetic. With
  `flagTempVarying` the sign is flipped below 370 K, so use a positive `A0` there if you want AFM at low
  temperature, as in FeRh.
- `Temp` is both the noise temperature (`flag0`) and the amplitude of the temperature cycle
  (`flagTempVarying`).
- The thermal field direction comes from `np.random.rand(3)`, so it always lies in the +x+y+z octant and
  has a non-zero mean.
- `H_Amp` is added to the DC field amplitude before the `flag3` branch. Keep `Hex_AC` orthogonal to
  `Hex_DC`.
- The chirp current is not multiplied by `SHE_angle`, unlike the SOT current.
- `p`, `T` and `gamma` are parsed but not used. `RK4` exists but the solver uses `Heun`.
- The defaults in `conf.py` (Ms, Ku, damping, SHE angle) are the AFM-PUF values, not FeRh.

## Configuration

Set these in `.env`.

| Variable | Default | Meaning |
|---|---|---|
| `ANTHROPIC_API_KEY` | empty | server-side Anthropic key; otherwise paste it in Settings |
| `OPENAI_API_KEY` | empty | server-side OpenAI key; otherwise paste it in Settings |
| `CLAUDE_MODEL` | `claude-sonnet-5` | default Claude model |
| `CLAUDE_MODELS` | built-in list | fallback Claude model list |
| `OPENAI_MODEL` | `gpt-4.1` | default OpenAI model |
| `OPENAI_MODELS` | built-in list | fallback OpenAI model list |
| `HOST_PORT` | `8421` | port on your machine |
| `MAX_PARALLEL` | CPU cores - 1 | simultaneous runs |
| `MAX_SWEEP_RUNS` | `400` | largest sweep |
| `TOKEN_BUDGET` | `0` (off) | token limit for the assistant per server session |
| `AGENT_MAX_STEPS` | `24` | tool steps the assistant may take for one message |
| `WORKSPACE` | `/data` in Docker | where runs, sweeps and files are stored |
| `SSL_CERT_FILE` | system bundle | CA bundle used for HTTPS, see below |

One Heun step takes roughly 0.6 to 1 ms on one core, so the default 10 ns (100 000 steps) takes one to two
minutes. The runtime estimate is recalibrated from finished runs.

## Behind a corporate proxy

If the container cannot reach `api.anthropic.com` you will see one of two messages in the chat:

- `CERTIFICATE_VERIFY_FAILED`: the network re-signs HTTPS traffic with a company certificate that the
  container does not know. Put the CA bundle in `certs/` and point `SSL_CERT_FILE` at it:

  ```bash
  mkdir -p certs
  cp /etc/ssl/certs/ca-certificates.crt certs/ca-bundle.crt     # a host that already trusts the CA
  echo 'SSL_CERT_FILE=/certs/ca-bundle.crt' >> .env
  docker compose up --build -d
  ```

  The `certs/` folder is mounted read-only and ignored by git.

- "The network returned a web page instead of the Claude/OpenAI API": a gateway or acceptable-use page is
  answering in place of the API. Open the address in a browser or ask your network team to allow it.
  The simulator itself and Run setup keep working in the meantime.

## How it works

```
browser (web/index.html)
   |  conversation kept in the page, sent every turn, answer streamed back as events
   v
FastAPI (app/server.py) --> provider-neutral tool loop (Claude or OpenAI; app/agent.py, app/tools.py)
   |                                   |  validate, start, sweep, get_run, cancel
   v                                   v
run queue (app/simulations.py) --> python -m app.runner   (one process per run)
                                       imports macrospin/main.py unchanged
```

`app/runner.py` wraps the original code without changing its equations. It counts Heun steps for the
progress bar, silences the per-step print of the temperature branch, adds a font fallback and writes the
summary. Parameter files are parsed with the original `importdata.py`. `app/guide.py` backs Athena, the guide.

## Development

```bash
pip install -r requirements-dev.txt
pytest -q
```

```
app/               server, agent loop, tools, run manager, runner, prompts, guide
macrospin/         original simulation code, unchanged
original_scripts/  original batch scripts (prepare, run_sim, avg_neel, neel_vector_generate)
web/index.html     the whole interface
examples/          parameter files
tests/             smoke tests
```

The server has no login. It is meant for local use; do not expose the port to the internet, since anyone
who can reach it can start simulations and use the server-side API keys.
