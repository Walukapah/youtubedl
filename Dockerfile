# Use Python 3.11 base image
FROM python:3.11-slim

# Set working directory
WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    build-essential \
    curl \
    git \
    libffi-dev \
    libssl-dev \
    nodejs \
    npm \
    && rm -rf /var/lib/apt/lists/*

# Install JupyterLab and common packages
RUN pip install --no-cache-dir \
    jupyterlab \
    notebook \
    ipywidgets \
    jupyterlab-git \
    jupyterlab-code-formatter \
    black \
    isort \
    numpy \
    pandas \
    matplotlib \
    seaborn \
    scikit-learn \
    requests \
    Pillow \
    plotly \
    nbformat

# Create jupyter config directory
RUN mkdir -p /root/.jupyter

# Generate jupyter config and set password/token
RUN jupyter lab --generate-config && \
    echo "c.ServerApp.token = ''" >> /root/.jupyter/jupyter_lab_config.py && \
    echo "c.ServerApp.password = ''" >> /root/.jupyter/jupyter_lab_config.py && \
    echo "c.ServerApp.allow_root = True" >> /root/.jupyter/jupyter_lab_config.py && \
    echo "c.ServerApp.ip = '0.0.0.0'" >> /root/.jupyter/jupyter_lab_config.py && \
    echo "c.ServerApp.port = 7860" >> /root/.jupyter/jupyter_lab_config.py && \
    echo "c.ServerApp.open_browser = False" >> /root/.jupyter/jupyter_lab_config.py && \
    echo "c.ServerApp.root_dir = '/app/notebooks'" >> /root/.jupyter/jupyter_lab_config.py && \
    echo "c.ServerApp.allow_origin = '*'" >> /root/.jupyter/jupyter_lab_config.py && \
    echo "c.ServerApp.disable_check_xsrf = True" >> /root/.jupyter/jupyter_lab_config.py

# Create notebooks directory
RUN mkdir -p /app/notebooks

# Expose port (Hugging Face default)
EXPOSE 7860

# Start JupyterLab
CMD ["jupyter", "lab", "--config=/root/.jupyter/jupyter_lab_config.py"]
