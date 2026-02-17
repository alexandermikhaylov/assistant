FROM node:22-slim AS bridge-builder

WORKDIR /build
RUN apt-get update && apt-get install -y git golang && rm -rf /var/lib/apt/lists/*

# Clone WhatsApp MCP (Bridge)
RUN git clone https://github.com/verygoodplugins/whatsapp-mcp.git
WORKDIR /build/whatsapp-mcp/whatsapp-bridge
RUN go build -o main .

# --- Runtime Stage ---
FROM python:3.11-slim

# Install Node.js (needed for Google Calendar MCP and Gemini CLI)
RUN apt-get update && apt-get install -y curl gnupg && \
    curl -fsSL https://deb.nodesource.com/setup_22.x | bash - && \
    apt-get install -y nodejs git procps && \
    rm -rf /var/lib/apt/lists/*

# Install expect for unbuffer
RUN apt-get update && apt-get install -y expect && \
    rm -rf /var/lib/apt/lists/*

RUN mkdir -p ~/.ssh && ssh-keyscan github.com >> ~/.ssh/known_hosts

# Install Gemini CLI
RUN npm install -g @google/gemini-cli@latest

# Install Python dependencies from requirements.txt
COPY requirements.txt /tmp/requirements.txt
RUN pip3 install --break-system-packages -r /tmp/requirements.txt && rm /tmp/requirements.txt

# Set working directory
WORKDIR /app

# Create directories for persistent data and user spaces
# Note: mcp-servers are now inside users/, so we don't create /app/mcp-servers globally
RUN mkdir -p /app/data /app/scripts /app/core_instructions /app/config /app/users /root/.gemini

# Copy entrypoint
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

ENTRYPOINT ["/entrypoint.sh"]
