// static/js/script.js

document.addEventListener('DOMContentLoaded', () => {
    const searchInput = document.getElementById('searchInput');
    // Changed selector to match the new generic class name
    const itemCards = document.querySelectorAll('.item-card');
    const categorySections = document.querySelectorAll('.category-section');
    // Get the no results message element
    const noResultsMessage = document.getElementById('noResultsMessage');

    if (searchInput && itemCards.length > 0 && categorySections.length > 0) {
        searchInput.addEventListener('input', (event) => {
            const searchTerm = event.target.value.toLowerCase().trim();
            let anyItemVisibleOverall = false;

            categorySections.forEach(section => {
                // Changed selector to match the new generic class name
                const itemsInSection = section.querySelectorAll('.item-card');
                let anyItemVisibleInSection = false;

                itemsInSection.forEach(card => {
                    const title = card.getAttribute('data-title'); // Assumes data-title exists and is lowercase
                    // Check if title is not null before calling includes
                    const isVisible = searchTerm === '' || (title && title.includes(searchTerm));
                    card.style.display = isVisible ? 'block' : 'none';
                    if (isVisible) {
                        anyItemVisibleInSection = true;
                        anyItemVisibleOverall = true;
                    }
                });

                // Hide/show the entire category section based on visible items within it
                section.style.display = anyItemVisibleInSection ? 'block' : 'none';
            });

            // Show or hide the "no results" message
            if (noResultsMessage) {
                noResultsMessage.style.display = anyItemVisibleOverall ? 'none' : 'block';
            }
        });

        // Initial filtering in case the search input has a value on load (e.g., browser back button)
        searchInput.dispatchEvent(new Event('input'));

    } else {
        if (!searchInput) console.error("Search input element not found.");
        if (itemCards.length === 0) console.warn("No item cards found on the page.");
        if (categorySections.length === 0) console.warn("No category sections found on the page.");
        // Don't hide noResultsMessage here, as it might be needed if items load async later
    }
});
