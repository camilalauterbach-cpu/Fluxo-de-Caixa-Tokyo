The working release builder encrypts the private application source and all
supplied references before adding them to a distributable Windows ZIP. Public
files contain only generic helpers, pinned Python/dependency provenance and
authenticated ciphertext. No private source or access key belongs in Git.

Run `python tools/build_working.py --help` for required private input paths.
The private key file contains a base64url encoding of 32 random bytes. Deliver
the key separately to the intended recipient. A fresh random nonce means
successive builds have different ciphertext and release SHA256 hashes.

The official PSF runtime SHA512 and PyPI wheel SHA256 pins are always verified.
The bootstrap verifies AES-GCM authentication, every inner file hash and the
exact manifest before staged installation. Existing `private/` databases are
preserved. Native Windows execution has not been validated in this environment.

The CMD entry point reopens itself in a persistent `cmd /D /K` console, disables
delayed expansion and returns with `exit /b`. Pasted newlines cannot dismiss an
error through a pending `pause` input. Package-key input stays inside the Python
bootstrap; no key is passed through shell arguments, environment or a key file.
The Windows key dialog uses a static PowerShell WinForms command, with visible
console input as fallback. Initialization logs record stages and error types
without the package key or financial contents.
