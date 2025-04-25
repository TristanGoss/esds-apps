const cardFrontModal = document.getElementById("cardFrontModal");
const cardFrontModalClose = document.getElementById("cardFrontModalClose");
const cardFrontModalloadingMessage = document.getElementById("cardFrontModalloadingMessage");
const cardFrontImage = document.getElementById("cardFrontImage");

const printModal = document.getElementById("printModal");
const printModalClose = document.getElementById("printModalClose");

// show Card Front modal
function showImageModal(cardNumber) {
    // Reset state
    cardFrontImage.style.display = "none";
    cardFrontModalloadingMessage.style.display = "block";
    cardFrontImage.src = `/membership-cards/${encodeURIComponent(cardNumber)}/card-front.png`;
    cardFrontModal.style.display = "block";
}

// Hide loading message and show image once card front available.
cardFrontImage.addEventListener("load", function () {
    cardFrontModalloadingMessage.style.display = "none";
    cardFrontImage.style.display = "block";
});

// Close card front modal.
cardFrontModalClose.addEventListener("click", function () {
    cardFrontModal.style.display = "none";
    cardFrontModalloadingMessage.style.display = "block";
    cardFrontImage.src = ""; // Reset image to avoid stale loads
});

// PDF download button triggers print layout modal.
document.getElementById("download-pdf-btn").addEventListener("click", function () {
    printModal.style.display = "block";
    addSelectedCardsToForm();
});

// Close print layout modal.
printModalClose.addEventListener("click", function () {
    printModal.style.display = "none";
});

// Close modals if the user clicks outside of them.
window.onclick = function(event) {
    if (event.target === printModal) {
        printModal.style.display = "none";
    }
    if (event.target === cardFrontModal) {
        cardFrontModal.style.display = "none";
        cardFrontModalloadingMessage.style.display = "block";
        cardFrontImage.src = ""; // Reset image to avoid stale loads
    }
};

// Remove non-selected cards from and add the selected cards to the form submitted by the print layout modal.
function addSelectedCardsToForm() {
    const form = document.getElementById("layout-form");
    const existingInputs = form.querySelectorAll('input[name="card_uuid"]');
    existingInputs.forEach(i => i.remove());

    const checkboxes = document.querySelectorAll('input[name="card_uuid"]:checked');
    checkboxes.forEach(cb => {
        const input = document.createElement("input");
        input.type = "hidden";
        input.name = "card_uuids";
        input.value = cb.value;
        form.appendChild(input);
    });
}

// Enable the download pdf button only if at least one card is selected.
document.addEventListener("DOMContentLoaded", function () {
    const checkboxes = document.querySelectorAll('input[name="card_uuid"]');
    const downloadBtn = document.getElementById("download-pdf-btn");

    function updateButtonState() {
        const anyChecked = Array.from(checkboxes).some(cb => cb.checked);
        downloadBtn.disabled = !anyChecked;
    }

    checkboxes.forEach(cb => {
        cb.addEventListener("change", updateButtonState);
    });

    updateButtonState(); // Set initial state
});

// Filter the table based on the provided first name.
document.addEventListener("DOMContentLoaded", function () {
    const input = document.getElementById("filter-first-name");
    const rows = document.querySelectorAll("tbody tr");

    input.addEventListener("input", function () {
        const filterValue = input.value.toLowerCase().trim();

        rows.forEach(row => {
            const firstNameCell = row.cells[2];  // First name is in the 3rd column
            const firstName = firstNameCell.textContent.toLowerCase();
            row.style.display = firstName.includes(filterValue) ? "" : "none";
        });
    });
});

// Ensure reissue button is only enabled if a reason is selected.
function updateReissueButton(cardUuid) {
    const reasonSelect = document.getElementById(`reason-${cardUuid}`);
    const button = document.getElementById(`reissue-btn-${cardUuid}`);
    button.disabled = !reasonSelect.value;
}

// Submit a request to reissue a card
function submitReissue(cardUuid, cardFirstName) {
    const reasonSelect = document.getElementById(`reason-${cardUuid}`);
    const reason = reasonSelect.value;

    if (!reason) return;

    const confirmMsg = `Are you sure you want to reissue the membership card belonging to ${cardFirstName} because it has been ${reason}?`;
    const confirmed = window.confirm(confirmMsg);

    if (!confirmed) return;

    // Create and submit a form dynamically
    const form = document.createElement("form");
    form.method = "POST";
    form.action = `/membership-cards/${cardUuid}/reissue`;

    const input = document.createElement("input");
    input.type = "hidden";
    input.name = "reason";
    input.value = reason;

    form.appendChild(input);
    document.body.appendChild(form);
    form.submit();
}

// Submit a request to cancel a card
function submitCancel(cardUuid, cardFirstName) {
    const confirmMsg = `Are you sure you want to cancel the membership card belonging to ${cardFirstName}?`;
    const confirmed = window.confirm(confirmMsg);

    if (!confirmed) return;

    // Create and submit a form dynamically
    const form = document.createElement("form");
    form.method = "POST";
    form.action = `/membership-cards/${cardUuid}/cancel`;

    document.body.appendChild(form);
    form.submit();
}
