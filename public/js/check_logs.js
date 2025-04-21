
// Filter the table based on the provided first name.
document.addEventListener("DOMContentLoaded", function () {
    const input = document.getElementById("filter-first-name");
    const rows = document.querySelectorAll("tbody tr");

    input.addEventListener("input", function () {
        const filterValue = input.value.toLowerCase().trim();

        rows.forEach(row => {
            const firstNameCell = row.cells[1];  // First name is in the 2nd column
            const firstName = firstNameCell.textContent.toLowerCase();
            row.style.display = firstName.includes(filterValue) ? "" : "none";
        });
    });
});