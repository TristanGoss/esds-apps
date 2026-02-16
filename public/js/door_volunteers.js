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


// Submit a request to add a volunteer to the group
function submitAdd(volunteer_email) {
    fetch('/door-volunteers/add', {
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


// Submit a request to remove a volunteer from the group
function submitRemove(volunteerUuid, volunteerFirstName) {
    const confirmMsg = `Are you sure you want to remove ${volunteerFirstName} from the Door Volunteers group? They will no longer be able to access pos.dancecloud.com`;
    const confirmed = window.confirm(confirmMsg);

    if (!confirmed) return;

    fetch(`/door-volunteers/${volunteerUuid}/remove`, {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
        },
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
    .catch(() => {
        alert('Failed to remove volunteer.');
    });
}
