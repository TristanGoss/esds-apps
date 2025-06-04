
const allDiv = document.getElementById("all");
const resultDiv = document.getElementById("result");
const scanner = new Html5Qrcode("reader");
const sounds = {
  valid: new Audio("/public/330047__paulmorek__beep-04-positive-2.mp3"),
  invalid: new Audio("/public/330049__paulmorek__beep-04-negative.mp3")
};
let readyForScan = true;
let lastDecodedText = null;
let isValidSoundLoaded = false;
let isInvalidSoundLoaded = false;

// Force browsers to preload
sounds.valid.load();
sounds.invalid.load();

// Force iOS browsers to preload
document.addEventListener("click", () => {
    sounds.valid.play().catch(() => {});
    sounds.invalid.play().catch(() => {});
  }, { once: true });

function showResult(message, isValid) {
  resultDiv.textContent = message;
  resultDiv.style.color = isValid ? "green" : "red";

  // Flash scanner background
  const flashClass = isValid ? "flash-green" : "flash-red";
  allDiv.classList.add(flashClass);
  setTimeout(() => allDiv.classList.remove(flashClass), 600); // flash for 600ms

  // Play sound
  const audioResponse = isValid ? sounds.valid : sounds.invalid;
  audioResponse.currentTime = 0;  // reset playback in case sound was already played
  audioResponse.play().catch(() => {});
}

async function validateCard(data) {
  try {
    const res = await fetch(`/proxy-card-check?url=${encodeURIComponent(data)}`);
    const text = await res.text();
    const valid = text.includes("This membership card is valid");
    showResult(valid ? "✅ CARD VALID" : "❌ CARD INVALID", valid);
  } catch (err) {
    showResult("❌ FAILED TO VALIDATE", false);
  }
}

// once sounds have loaded, enable scanner
sounds.valid.addEventListener("canplaythrough", () => {
  isValidSoundLoaded = true;
  console.log("DEBUG A");
  enableScannerIfReady();
})

sounds.invalid.addEventListener("canplaythrough", () => {
  isInvalidSoundLoaded = true;
  console.log("DEBUG B");
  enableScannerIfReady();
})

// we don't enable the scanner until both sounds are loaded.
// This prevents immediate scans (when the page is refreshed while pointing at a PR code)
// from failing to sound, and hints to the user that refreshing the page all the time isn't a good idea.
function enableScannerIfReady() {
  console.log("enableScannerIfReady called");
  if (isInvalidSoundLoaded && isValidSoundLoaded) {
    scanner.start(
      { facingMode: "environment" },
      {
        fps: 10,
        qrbox: { width: 1000, height: 1000 }
      },
      (decodedText, decodedResult) => {
        if (!readyForScan || decodedText === lastDecodedText) return;
        readyForScan = false; // prevent repeated scans until no QR code is detected.
        lastDecodedText = decodedText
        validateCard(decodedText).then(() => {
            setTimeout(() => {lastDecodedText = null}, 4000) // debounce repeated scans of the same code
        });
      },
      (errorMessage) => {
        // If nothing is detected (i.e. the code has been removed from view) we may ready for another scan
        readyForScan = true;
      }
    );
  }
}
