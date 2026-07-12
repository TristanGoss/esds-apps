
// Filter the table by first name and, optionally, hide checks of expired / invalidated cards.
document.addEventListener("DOMContentLoaded", function () {
    const input = document.getElementById("filter-first-name");
    const showInvalidated = document.getElementById("show-invalidated");
    const rows = document.querySelectorAll("tbody tr");
    const validCount = document.getElementById("scan-count-valid");
    const allCount = document.getElementById("scan-count-all");

    function applyFilters() {
        const filterValue = input.value.toLowerCase().trim();
        const includeInvalidated = showInvalidated.checked;

        rows.forEach(row => {
            const firstName = row.cells[1].textContent.toLowerCase();  // First name is in the 2nd column
            const invalidated = row.dataset.invalidated === "true";
            const visible = firstName.includes(filterValue) && (includeInvalidated || !invalidated);
            row.style.display = visible ? "" : "none";
        });

        // The last-hour counter tracks the toggle: valid-only cards, or every scanned card.
        validCount.hidden = includeInvalidated;
        allCount.hidden = !includeInvalidated;
    }

    input.addEventListener("input", applyFilters);
    showInvalidated.addEventListener("change", applyFilters);
    applyFilters();
});
