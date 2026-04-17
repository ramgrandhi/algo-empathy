# algo-empathy: "Do I Need a Coat?" 🧥

Algorithmic Empathy: A mobile-compatible voice weather web app that determines if you need a coat based on your destination and the weather, while silently capturing t-NPS training data.

## Features

- **Voice Activity Detection**: Browser-side VAD capturing audio without manual stops.
- **Emotion Recognition**: Transcribes and scores user emotion dynamically via HuggingFace's XLS-R model.
- **Autonomous LLM Conversation**: Uses Claude Haiku (Anthropic API) to reason conversation turns up to a max of 5, providing nuanced advice.
- **t-NPS Capture**: A post-conversation feedback tap widget.
- **Local Persistence**: Stores voice sessions and feedback data locally in your `~/data` folder.

## Prerequisites

- **Docker** and **Docker Compose** installed on your machine.
- API Keys:
  - OpenWeatherMap API Key
  - Anthropic API Key (for Claude 3.5 Haiku) or Azure OpenAI keys.

## Setup & Running the Project

1. **Clone the repository and enter the directory**:
   ```bash
   git clone https://github.com/your-username/algo-empathy.git
   cd algo-empathy
   ```

2. **Configure Environment Variables**:
   Copy the example environment file and fill in your API keys:
   ```bash
   cp .env.example .env
   ```
   *Ensure you populate `OPENWEATHER_API_KEY`, `LLM_PROVIDER=claude`, and `ANTHROPIC_API_KEY` (or the respective Azure keys).*

3. **Build and Start the Containers**:
   To build the Docker images (which will pre-download the necessary ML models) and start the services in detached mode, run:
   ```bash
   docker-compose build
   docker-compose up -d
   ```
   *Note: The first build will take some time as it downloads the Whisper (~74 MB) and wav2vec2 (~1.2 GB) models.*

4. **Access the Application**:
   - **Frontend (Web App)**: [http://localhost:3001](http://localhost:3001)
   - **Backend (FastAPI)**: [http://localhost:8000](http://localhost:8000)

## Managing the Application

- **View Logs**:
  ```bash
  # View all logs
  docker-compose logs -f

  # View backend or frontend logs specifically
  docker-compose logs -f backend
  docker-compose logs -f frontend
  ```

- **Restart a specific service after a code change**:
  ```bash
  docker-compose build backend && docker-compose up -d backend
  # OR
  docker-compose build frontend && docker-compose up -d frontend
  ```

- **Stop the application**:
  ```bash
  docker-compose down
  ```

## Data Storage

Your session data, including the conversational JSON payloads and `.wav` audio files for each turn, are saved directly to your host machine at:
```bash
~/data/sessions/
```
The machine learning model weights are stored in the `./models` directory within the repository so they don't need to be redownloaded.
