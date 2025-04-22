
const resultDiv = document.getElementById("result");
const scanner = new Html5Qrcode("reader");

let readyForScan = true;
let lastDecodedText = null;

function showResult(message, isValid) {
  resultDiv.textContent = message;
  resultDiv.style.color = isValid ? "green" : "red";
  const audioPath = isValid ? "/public/330047__paulmorek__beep-04-positive-2.wav" : "/public/330049__paulmorek__beep-04-negative.wav";
  new Audio(audioPath).play().catch(() => {});
}

async function validateCard(data) {
  try {
    const res = await fetch(data);
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
    qrbox: { width: 500, height: 500 }
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