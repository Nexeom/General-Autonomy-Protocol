# Security Policy

## Reporting a Vulnerability

The GAP protocol governs autonomous AI systems. Security is foundational, not optional.

If you discover a security vulnerability in the GAP reference implementation or identify a weakness in the protocol specification, **please report it responsibly.**

### How to Report

**Email:** security@nexeom.com

Please include:
- Description of the vulnerability
- Steps to reproduce (if applicable)
- Potential impact assessment
- Suggested mitigation (if you have one)

### What to Expect

- **Acknowledgment** within 48 hours
- **Assessment** within 7 days
- **Resolution timeline** communicated within 14 days
- **Credit** in the security advisory (unless you prefer anonymity)

### Do Not

- Open a public GitHub issue for security vulnerabilities
- Exploit the vulnerability against production systems
- Share the vulnerability publicly before a fix is available

## Scope

This policy covers:
- The GAP reference implementation (`gap_kernel/`)
- The protocol specification (`docs/SPECIFICATION.md`)
- Structural enforcement guarantees (Iron Rule, governance isolation)
- Decision Record integrity (cryptographic chaining)

## Security Design Principles

GAP is designed with security as an architectural property:

- **Structural enforcement** over policy enforcement — governed agents cannot access governance infrastructure
- **Cryptographic integrity** on Decision Records — tamper-evident audit trails
- **Separation of Creation and Validation** — independent evaluation prevents self-certification
- **Action Type Registry** — unregistered action types are rejected, preventing capability creep

We welcome adversarial analysis of these guarantees.
