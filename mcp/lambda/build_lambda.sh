#!/bin/bash
# Build Lambda deployment package with dependencies

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
BUILD_DIR="${SCRIPT_DIR}/build"
ZIP_FILE="${SCRIPT_DIR}/../../mcp_lambda.zip"

echo "Building Lambda package..."

# Clean up
rm -rf "${BUILD_DIR}"
mkdir -p "${BUILD_DIR}"

# Install dependencies
pip install -r "${SCRIPT_DIR}/requirements.txt" -t "${BUILD_DIR}" --platform manylinux2014_x86_64 --only-binary=:all: --python-version 3.12

# Copy handler
cp "${SCRIPT_DIR}/handler.py" "${BUILD_DIR}/"

# Create zip
cd "${BUILD_DIR}"
zip -r "${ZIP_FILE}" .

echo "Lambda package created: ${ZIP_FILE}"