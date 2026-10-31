# Security Policy

## Project status

LogSlice is at version 0.1 and has not been published to a package index. This policy applies to the code in this repository.

## Supported versions

| Version | Supported |
|---|---|
| Latest commit on `main` | Yes |
| Older releases | Only the latest minor release receives security fixes |

## Reporting a vulnerability

**Please do not open public GitHub issues for security vulnerabilities.**

Report privately using GitHub's private vulnerability reporting:

1. Go to [https://github.com/yagnang-vyas/log-slice/security](https://github.com/yagnang-vyas/log-slice/security).
2. Click **Report a vulnerability**.
3. Include a description, affected component and version (or commit), steps to reproduce, and the potential impact.

If private reporting is unavailable, contact the maintainer, **Yagnang Vyas**, through GitHub ([https://github.com/yagnang-vyas](https://github.com/yagnang-vyas)) and ask for a private channel. Do not include exploit details in public messages.

## What to expect

| Step | Target time |
|---|---|
| Acknowledgement of your report | Within 5 business days |
| Initial assessment | Within 10 business days |
| Fix or mitigation plan | Depends on severity; critical issues are prioritized |

You will be kept informed of progress. With your permission, you will be credited in the release notes once a fix is published.

## Areas of particular interest

- Any network access by the tool
- Bypass of the default restriction on DuckDB external file access after ingest
- Crafted log files causing code execution or unbounded memory use

## Out of scope

- Vulnerabilities in third-party dependencies that are already publicly known, unless LogSlice uses them in an exploitable way.
- Issues requiring physical access to a user's device.
- Social engineering of the maintainer or users.

## Safe harbor

Good-faith security research that follows this policy, avoids privacy violations and service disruption, and gives reasonable time to fix issues before disclosure is welcome and will not be pursued.
