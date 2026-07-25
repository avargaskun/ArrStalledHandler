# Use an official Python runtime as the base image
FROM python:3.13-slim

# Set the working directory
WORKDIR /app

# Copy the script and dependencies
COPY . /app

# Install Python dependencies
RUN pip install -r requirements.txt

# Make the script executable
RUN chmod +x main.py

# Disable output buffering in Python
ENV PYTHONUNBUFFERED=1

# Health check endpoint (/ping)
EXPOSE 9898

# Specify the entrypoint
ENTRYPOINT ["python", "main.py"]
