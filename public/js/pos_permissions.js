const cardFrontModal = document.getElementById("cardFrontModal");
const cardFrontModalClose = document.getElementById("cardFrontModalClose");
const cardFrontModalloadingMessage = document.getElementById("cardFrontModalloadingMessage");
const cardFrontImage = document.getElementById("cardFrontImage");

const printModal = document.getElementById("printModal");
const printModalClose = document.getElementById("printModalClose");


// Filter the table based on the provided first name.
document.addEventListener("DOMContentLoaded", function () {
    const input = document.getElementById("filter-first-name");
    const rows = document.querySelectorAll("tbody tr");

    input.addEventListener("input", function () {
        const filterValue = input.value.toLowerCase().trim();

        rows.forEach(row => {
            const firstNameCell = row.cells[0];  // First name is in the 1st column
            const firstName = firstNameCell.textContent.toLowerCase();
            row.style.display = firstName.includes(filterValue) ? "" : "none";
        });
    });
});


// Submit a request to give a volunteer access to pos.dancecloud.com
function submitAdd(volunteer_email) {
    fetch('/pos-permissions/add', {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
        },
        body: JSON.stringify({ volunteer_email }),
    })
    .then(response => {
        if (response.redirected) {
            window.location.href = response.url;
        } else if (response.ok) {
            window.location.reload();
        } else {
            alert('Failed to add volunteer.');
        }
    })
    .catch(() => {
        alert('Failed to add volunteer.');
    });
}


// Submit a request to remove Dancecloud POS permissions from a volunteer
document.addEventListener('click', function (e) {
    const btn = e.target.closest('.remove-btn');
    if (!btn) return;

    const volunteerUuid = btn.dataset.uuid;
    const volunteerFirstName = btn.dataset.name;
    const confirmMsg = `Are you sure you want to remove Dancecloud POS permissions from ${volunteerFirstName}?`;
    if (!window.confirm(confirmMsg)) return;

    fetch(`/pos-permissions/${encodeURIComponent(volunteerUuid)}/remove`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
    })
    .then(response => {
        if (response.redirected) {
            window.location.href = response.url;
        } else if (response.ok) {
            window.location.reload();
        } else {
            alert('Failed to remove volunteer.');
        }
    })
    .catch(() => { alert('Failed to remove volunteer.'); });
});
