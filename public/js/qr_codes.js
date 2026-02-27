// Filter the QR code table by description

document.addEventListener("DOMContentLoaded", function () {
    const input = document.getElementById("filter-description");
    if (!input) return;
    const rows = document.querySelectorAll("tbody tr");

    input.addEventListener("input", function () {
        const filterValue = input.value.toLowerCase().trim();
        rows.forEach(row => {
            const descCell = row.cells[0]; // Description is in the first column
            const desc = descCell.textContent.toLowerCase();
            row.style.display = desc.includes(filterValue) ? "" : "none";
        });
    });
});
