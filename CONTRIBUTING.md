# Contributing to GAP

Thank you for your interest in contributing to the General Autonomy Protocol. GAP is an open standard, and community input is essential to making it robust, practical, and widely adopted.

## How to Contribute

### Reporting Issues

- Use [GitHub Issues](https://github.com/Nexeom/General-Autonomy-Protocol/issues) for bug reports, protocol questions, and feature requests
- For security vulnerabilities, please email security@nexeom.ca directly — do not open a public issue

### Protocol Feedback

GAP is a living protocol. We actively seek feedback on:

- **Specification gaps** — mechanisms or scenarios the protocol doesn't adequately address
- **Implementation challenges** — difficulties you encounter building GAP-compliant systems
- **Domain-specific extensions** — Action Type Registry configurations for new domains
- **Adversarial analysis** — attempts to circumvent or weaken governance guarantees

Open a [Discussion](https://github.com/Nexeom/General-Autonomy-Protocol/discussions) for protocol-level feedback.

### Code Contributions

1. **Fork** the repository
2. **Create a branch** from `main` (`git checkout -b feature/your-feature`)
3. **Write tests** — all contributions must include tests. Current suite: 111 tests, 0 failures.
4. **Follow existing patterns** — Pydantic models for data structures, pytest for testing
5. **Submit a Pull Request** with a clear description of what changed and why

### Documentation

Documentation improvements are always welcome.

## Development Setup

```bash
# Clone your fork
git clone https://github.com/YOUR-USERNAME/General-Autonomy-Protocol.git
cd General-Autonomy-Protocol

# Create a virtual environment
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install in development mode
pip install -e .

# Run the test suite
pytest tests/ -v

# All 111 tests should pass before you submit a PR
```

## Code Standards

- **Python 3.11+** required
- **Pydantic** for all data models — typed, validated, serializable
- **LangGraph** for state machine implementations
- **Type hints** on all function signatures
- Clear docstrings on public APIs

## Protocol Governance

Changes to the core protocol specification (Governance Kernel behavior, Decision Record schema, Iron Rule enforcement, authorization levels) require review by the protocol maintainers. This is intentional — the stability and reliability of the governance standard is itself a governance concern.

Additive changes (new Action Type configurations, domain-specific extensions, additional examples) have a lower review bar.

## License

By contributing, you agree that your contributions will be licensed under the [Apache License 2.0](LICENSE).

## Code of Conduct

All participants are expected to follow our [Code of Conduct](CODE_OF_CONDUCT.md).

---

Questions? Open a [Discussion](https://github.com/Nexeom/General-Autonomy-Protocol/discussions) or reach out at contribute@nexeom.ca.
