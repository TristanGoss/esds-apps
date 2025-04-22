
const resultDiv = document.getElementById("result");
const scanner = new Html5Qrcode("reader");
const sounds = {
  valid: new Audio("/public/330047__paulmorek__beep-04-positive-2.wav"),
  invalid: new Audio("/public/330049__paulmorek__beep-04-negative.wav")
};
let readyForScan = true;
let lastDecodedText = null;

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
  const audioResponse = isValid ? sounds.valid : sounds.invalid;
  audioResponse.currentTime = 0;  // reset playback in case sound was already played
  audioResponse.play().catch(() => {});
}

async function validateCard(data) {
  try {
    const res = await fetch(`/anti-cors-proxy?url=${encodeURIComponent(data)}`);
    const text = await res.text();
    const valid = text.includes("This membership card is valid");
    showResult(valid ? "✅ CARD VALID" : "❌ CARD INVALID", valid);
  } catch (err) {
    showResult("❌ FAILED TO VALIDATE", false);
  }
}

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