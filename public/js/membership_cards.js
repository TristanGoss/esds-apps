const cardFrontModal = document.getElementById("cardFrontModal");
const cardFrontModalClose = document.getElementById("cardFrontModalClose");
const cardFrontModalloadingMessage = document.getElementById("cardFrontModalloadingMessage");
const cardFrontImage = document.getElementById("cardFrontImage");

const printModal = document.getElementById("printModal");
const printModalClose = document.getElementById("printModalClose");

const reAuthModal = document.getElementById("reAuthModal");
const reAuthModalClose = document.getElementById("reAuthModalClose");
const reAuthForm = document.getElementById("reAuthForm");
const reAuthReason = document.getElementById("reAuthReason");
const reAuthModalMessage = document.getElementById("reAuthModalMessage");
const reAuthModalTitle = document.getElementById("reAuthModalTitle");

// show Card Front modal
function showImageModal(cardNumber) {
    // Reset state
    cardFrontImage.style.display = "none";
    cardFrontModalloadingMessage.style.display = "block";
    cardFrontImage.src = `/membership-cards/${encodeURIComponent(cardNumber)}/card-front.png`;
    cardFrontModal.showModal();
}

// Hide loading message and show image once card front available.
cardFrontImage.addEventListener("load", function () {
    cardFrontModalloadingMessage.style.display = "none";
    cardFrontImage.style.display = "block";
});

// Close card front modal.
cardFrontModalClose.addEventListener("click", function () {
    cardFrontModal.close();
    cardFrontModalloadingMessage.style.display = "block";
    cardFrontImage.src = ""; // Reset image to avoid stale loads
});

// Close card front modal on backdrop click.
cardFrontModal.addEventListener("click", function (e) {
    if (e.target === cardFrontModal) {
        cardFrontModal.close();
        cardFrontModalloadingMessage.style.display = "block";
        cardFrontImage.src = "";
    }
});

// PDF download button triggers print layout modal.
document.getElementById("download-pdf-btn").addEventListener("click", function () {
    addSelectedCardsToForm();
    printModal.showModal();
});

// Close print layout modal.
printModalClose.addEventListener("click", function () {
    printModal.close();
});

// Close print layout modal on backdrop click.
printModal.addEventListener("click", function (e) {
    if (e.target === printModal) {
        printModal.close();
    }
});

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

// Filter the table by first name and, optionally, hide expired / invalidated cards.
document.addEventListener("DOMContentLoaded", function () {
    const input = document.getElementById("filter-first-name");
    const showInvalidated = document.getElementById("show-invalidated");
    const rows = document.querySelectorAll("tbody tr");

    function applyFilters() {
        const filterValue = input.value.toLowerCase().trim();
        const includeInvalidated = showInvalidated.checked;

        rows.forEach(row => {
            const firstName = row.cells[2].textContent.toLowerCase();  // First name is in the 3rd column
            const invalidated = row.dataset.invalidated === "true";
            const visible = firstName.includes(filterValue) && (includeInvalidated || !invalidated);
            row.style.display = visible ? "" : "none";
        });
    }

    input.addEventListener("input", applyFilters);
    showInvalidated.addEventListener("change", applyFilters);
    applyFilters();
});

// Ensure reissue button is only enabled if a reason is selected.
document.addEventListener('change', function (e) {
    if (!e.target.classList.contains('reason-select')) return;
    const cardUuid = e.target.dataset.uuid;
    const button = document.getElementById(`reissue-btn-${cardUuid}`);
    button.disabled = !e.target.value;
});

// Open the confirmation modal for the given action.
function openReAuthModal(title, message, actionUrl, reason) {
    reAuthModalTitle.textContent = title;
    reAuthModalMessage.textContent = message;
    reAuthForm.action = actionUrl;
    reAuthReason.value = reason || '';
    reAuthModal.showModal();
}

// Close re-auth modal.
reAuthModalClose.addEventListener("click", function () {
    reAuthModal.close();
});

// Close re-auth modal on backdrop click.
reAuthModal.addEventListener("click", function (e) {
    if (e.target === reAuthModal) {
        reAuthModal.close();
    }
});

// Reissue and cancel buttons use event delegation to avoid inline JS with user data.
document.addEventListener('click', function (e) {
    const reissueBtn = e.target.closest('.reissue-btn');
    if (reissueBtn) {
        const cardUuid = reissueBtn.dataset.uuid;
        const cardFirstName = reissueBtn.dataset.name;
        const reasonSelect = document.getElementById(`reason-${cardUuid}`);
        const reason = reasonSelect ? reasonSelect.value : '';
        if (!reason) return;
        openReAuthModal(
            'Confirm Reissue',
            `You are about to reissue the membership card belonging to ${cardFirstName} because it has been ${reason}.`,
            `/membership-cards/${cardUuid}/reissue`,
            reason
        );
        return;
    }

    const cancelBtn = e.target.closest('.cancel-btn');
    if (cancelBtn) {
        const cardUuid = cancelBtn.dataset.uuid;
        const cardFirstName = cancelBtn.dataset.name;
        openReAuthModal(
            'Confirm Cancellation',
            `You are about to cancel the membership card belonging to ${cardFirstName}. This cannot be undone.`,
            `/membership-cards/${cardUuid}/cancel`,
            ''
        );
    }
});
