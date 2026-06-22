// Client-side decryption of dancer names for the /attendance charts. The server only ever sends
// Fernet ciphertext plus the non-secret salt/sentinel (from /attendance/decrypt-params); the
// operator's passphrase is turned into the decryption key here, in the browser, and used to
// decrypt names locally. This mirrors the Python scheme in attendance/pseudonyms_db.py exactly:
//
//   raw  = PBKDF2-SHA256(passphrase, salt, 480_000, 64 bytes)
//   Fernet key = raw[0:32]  ->  signing key = raw[0:16], AES-128 key = raw[16:32]
//   token (urlsafe-b64) = 0x80 | timestamp(8) | IV(16) | AES-128-CBC ciphertext | HMAC-SHA256(32)
//
// Nothing is persisted: the passphrase string is discarded the instant the keys are derived, the
// keys live only as non-extractable CryptoKeys in the closure below, and nothing touches
// localStorage/sessionStorage. Closing the tab (or reloading) forgets everything.

const AttendanceCrypto = (() => {
  const enc = new TextEncoder();
  const dec = new TextDecoder();
  const SENTINEL = 'esds-pseudonymise-v1'; // what the meta sentinel decrypts to (see pseudonyms_db.py)

  let keys = null; // { aesKey, hmacKey } as non-extractable CryptoKeys, or null when locked
  let params = null; // { saltBytes, sentinelToken } fetched once from the server

  function hexToBytes(hex) {
    const out = new Uint8Array(hex.length / 2);
    for (let i = 0; i < out.length; i++) out[i] = parseInt(hex.substr(i * 2, 2), 16);
    return out;
  }

  function b64urlToBytes(s) {
    const b64 = s.replace(/-/g, '+').replace(/_/g, '/');
    const bin = atob(b64);
    const out = new Uint8Array(bin.length);
    for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
    return out;
  }

  async function fetchParams() {
    if (params) return params;
    const resp = await fetch('/attendance/decrypt-params', { credentials: 'same-origin', cache: 'no-store' });
    if (!resp.ok) throw new Error('Could not load the decryption parameters.');
    const j = await resp.json();
    if (!j.salt || !j.sentinel) throw new Error('The database has no encryption parameters.');
    params = { saltBytes: hexToBytes(j.salt), sentinelToken: j.sentinel };
    return params;
  }

  async function deriveKeys(passphrase, saltBytes) {
    const baseKey = await crypto.subtle.importKey('raw', enc.encode(passphrase), 'PBKDF2', false, ['deriveBits']);
    const bits = new Uint8Array(
      await crypto.subtle.deriveBits({ name: 'PBKDF2', salt: saltBytes, iterations: 480000, hash: 'SHA-256' }, baseKey, 512)
    );
    const signingKey = bits.slice(0, 16);
    const aesKeyBytes = bits.slice(16, 32);
    const aesKey = await crypto.subtle.importKey('raw', aesKeyBytes, { name: 'AES-CBC' }, false, ['decrypt']);
    const hmacKey = await crypto.subtle.importKey('raw', signingKey, { name: 'HMAC', hash: 'SHA-256' }, false, ['verify']);
    bits.fill(0); // don't leave the raw key material lying around in the buffer
    return { aesKey, hmacKey };
  }

  async function fernetDecrypt(token, k) {
    const data = b64urlToBytes(token);
    const body = data.subarray(0, data.length - 32);
    const mac = data.subarray(data.length - 32);
    // HMAC is the deterministic correctness check: a wrong passphrase yields a wrong signing key
    // and the verify fails cleanly, rather than relying on AES padding luck.
    const ok = await crypto.subtle.verify('HMAC', k.hmacKey, mac, body);
    if (!ok) throw new WrongPassphraseError();
    const iv = data.subarray(9, 25);
    const ct = data.subarray(25, data.length - 32);
    const plain = await crypto.subtle.decrypt({ name: 'AES-CBC', iv }, k.aesKey, ct);
    return dec.decode(plain);
  }

  class WrongPassphraseError extends Error {}

  // Derive keys from the passphrase and confirm them against the sentinel. Throws
  // WrongPassphraseError on a bad passphrase; leaves the module unlocked on success.
  async function unlock(passphrase) {
    const p = await fetchParams();
    const k = await deriveKeys(passphrase, p.saltBytes);
    let plain;
    try {
      plain = await fernetDecrypt(p.sentinelToken, k);
    } catch (e) {
      throw new WrongPassphraseError();
    }
    if (plain !== SENTINEL) throw new WrongPassphraseError();
    keys = k;
  }

  function isUnlocked() {
    return keys !== null;
  }

  function lock() {
    keys = null;
  }

  // Decrypt one enc_name ciphertext to its fields object ({first_name, last_name, ...}), or null
  // for a null/empty token. Throws if the module is locked.
  async function decryptName(token) {
    if (!keys) throw new Error('locked');
    if (!token) return null;
    return JSON.parse(await fernetDecrypt(token, keys));
  }

  // --- unlock state subscribers ----------------------------------------------------------------
  // Charts register here so they can re-render when names are unlocked or re-locked: the retention
  // legend swaps to first names, the download panels switch from a hint to a working link.

  const subscribers = [];

  function onChange(callback) {
    subscribers.push(callback);
  }

  function notify() {
    subscribers.forEach((cb) => cb(isUnlocked()));
  }

  // --- top-of-page control ---------------------------------------------------------------------
  // One control near the top of the page (defined in the template) takes the passphrase once for
  // the whole session. On success everything re-renders; locking again reverts to pseudonyms.

  function wireControl() {
    const form = document.getElementById('passphrase-form');
    const input = document.getElementById('passphrase-input');
    const error = document.getElementById('passphrase-error');
    const submit = document.getElementById('passphrase-submit');
    const lockBtn = document.getElementById('passphrase-lock');
    const status = document.getElementById('passphrase-status-text');
    if (!form || !input) return; // control not on this page

    const refresh = () => {
      const unlocked = isUnlocked();
      input.hidden = unlocked;
      submit.hidden = unlocked;
      if (lockBtn) lockBtn.hidden = !unlocked;
      if (status) {
        status.textContent = unlocked
          ? 'PII is decrypted. Downloads include first and last names, and the retention legend shows teachers by first name.'
          : 'PII remains encrypted until you enter the passphrase.';
      }
    };

    form.addEventListener('submit', async (ev) => {
      ev.preventDefault();
      error.hidden = true;
      submit.setAttribute('aria-busy', 'true');
      try {
        await unlock(input.value);
        input.value = ''; // never leave the passphrase in the DOM
        refresh();
        notify();
      } catch (e) {
        input.value = '';
        input.focus();
        error.textContent =
          e instanceof WrongPassphraseError
            ? 'That passphrase did not match. Please try again.'
            : 'Could not unlock: ' + e.message;
        error.hidden = false;
      } finally {
        submit.removeAttribute('aria-busy');
      }
    });

    if (lockBtn) {
      lockBtn.addEventListener('click', () => {
        lock();
        refresh();
        notify();
      });
    }

    refresh();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', wireControl);
  } else {
    wireControl();
  }

  // --- CSV download ----------------------------------------------------------------------------
  // Build a CSV in memory and trigger a download, revoking the object URL afterwards. The CSV only
  // exists transiently in the browser; it is never sent anywhere.

  function csvCell(value) {
    const s = value === null || value === undefined ? '' : String(value);
    return /[",\r\n]/.test(s) ? '"' + s.replace(/"/g, '""') + '"' : s;
  }

  function downloadCsv(filename, header, rows) {
    const lines = [header, ...rows].map((r) => r.map(csvCell).join(','));
    const blob = new Blob([lines.join('\r\n')], { type: 'text/csv' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
  }

  return { isUnlocked, lock, onChange, decryptName, downloadCsv, WrongPassphraseError };
})();
