# Contributing to Digital Photo Frame

Thank you for your interest in contributing to Digital Photo Frame! We welcome contributions from everyone.

## Getting Started

1.  **Fork the repository** on GitHub.
2.  **Clone your fork** locally:
    ```bash
    git clone https://github.com/your-username/DigitalPhotoFrame.git
    cd DigitalPhotoFrame
    ```
3.  **Create a virtual environment**:
    ```bash
    python3 -m venv env
    source env/bin/activate
    pip install -e .
    ```
4.  **Create a branch** for your feature or bugfix:
    ```bash
    git checkout -b feature/my-new-feature
    ```

## Development Guidelines

### Coding Style

- We use [Ruff](https://github.com/astral-sh/ruff) for linting and formatting.
- Please ensure your code follows PEP 8 standards.
- Run the linter before committing:
  ```bash
  ruff check .
  ```

### Testing

- We use `pytest` for testing.
- Run all tests to ensure no regressions:
  ```bash
  pytest
  ```

### Commit Messages

- Use clear and descriptive commit messages.
- Reference issue numbers where applicable.

## Pull Request Process

1.  Ensure all tests pass.
2.  Update documentation if you're changing functionality.
3.  Submit a Pull Request to the `main` branch.
4.  Provide a clear description of the changes and the problem they solve.

## Code of Conduct

Please note that this project is released with a [Code of Conduct](CODE_OF_CONDUCT.md). By participating in this project you agree to abide by its terms.
