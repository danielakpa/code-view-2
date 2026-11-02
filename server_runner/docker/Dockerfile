# ==========================================
# CODEVIEW 2.0 - CLOUD DEVELOPMENT ENVIRONMENT
# ==========================================

# Use Debian with Go already installed.
FROM golang:1.24-bookworm

# Prevent interactive installation prompts.
ENV DEBIAN_FRONTEND=noninteractive

# ==========================================
# INSTALL PROGRAMMING LANGUAGES AND TOOLS
# ==========================================

# Install commonly used development tools.
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        python3 \
        python3-pip \
        python3-venv \
        nodejs \
        npm \
        git \
        curl \
        ca-certificates \
        bash \
        nano \
        procps \
        tini \
        build-essential \
        default-jdk \
        php-cli \
        ruby \
        && rm -rf /var/lib/apt/lists/*

# ==========================================
# INSTALL CODE-SERVER
# ==========================================

# Install code-server.
# For production, pin and verify a specific release.
RUN curl -fsSL https://code-server.dev/install.sh \
        -o /tmp/install-code-server.sh && \
    sh /tmp/install-code-server.sh \
        --method=standalone \
        --prefix=/usr/local && \
    rm /tmp/install-code-server.sh

# ==========================================
# CREATE DEVELOPER USER
# ==========================================

# Developers must not run as root.
RUN useradd \
    --create-home \
    --shell /bin/bash \
    --uid 10001 \
    developer

# ==========================================
# PREPARE WORKSPACE
# ==========================================

# Create persistent mount locations.
RUN mkdir -p /workspace /home/developer/.local \
    /home/developer/.config \
    /home/developer/.cache && \
    chown -R developer:developer \
        /workspace \
        /home/developer

# ==========================================
# ENVIRONMENT VARIABLES
# ==========================================

ENV HOME=/home/developer
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

# Default directory opened by the IDE.
WORKDIR /workspace

# ==========================================
# SECURITY
# ==========================================

# Run code-server as a non-root user.
USER developer

# Internal IDE port.
# EXPOSE does not publish the port publicly.
EXPOSE 8080

# Manage child processes and shutdown signals.
ENTRYPOINT ["/usr/bin/tini", "--"]

# ==========================================
# START CODE-SERVER
# ==========================================

# Start the IDE.
# A password must be supplied through environment
# configuration when the container is launched.
CMD ["code-server", \
     "--bind-addr", "0.0.0.0:8080", \
     "--auth", "password", \
     "--disable-telemetry", \
     "/workspace"]