# MUNDRA - MUNSoc Delegate Resource Application (1.0.0)

## Named after Mundra Port, Kutch, Gujarat, MUNDRA - MUNSoc Delegate Resource Application is a centralized database designed to optimize event planning, streamline communication, and facilitate delegate management

### Deployment environments

| Environment | Branch | Documentation URL                  |
| ----------- | ------ | ---------------------------------- |
| PROD        | main   | https://mundra.nnisarg.in/docs     |
| DEV         | dev    | https://mundra-dev.nnisarg.in/docs |

### Setup

1. Clone the repository:

```bash
git clone https://github.com/nnisarggada/mundra
cd mundra
```

2. Create a virtual environment called `env`

```bash
python -m venv env
```

3. Activate the virtual environment

```bash
source env/bin/activate
```

4. Install the dependencies

```bash
pip install -r requirements.txt
```

5. Run the development server

```bash
fastapi dev app.py
```
