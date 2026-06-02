FROM python:3.12-slim

WORKDIR /app

# Install build dependencies
RUN pip install --no-cache-dir --upgrade pip setuptools wheel

# Copy project files
COPY pyproject.toml README.md LICENSE.md ./
COPY src/ ./src/
COPY assets/ ./assets/

# Install the package
RUN pip install --no-cache-dir .

# Expose default port for SSE transport (Glama compatibility)
EXPOSE 8000

# Run the MCP server
ENTRYPOINT ["litvm-tcg-oracle"]
