(function () {
    "use strict";

    document.addEventListener(
        "submit",
        function (event) {

            const form = event.target;

            if (!(form instanceof HTMLFormElement)) {
                return;
            }

            const message =
                form.dataset.confirm;

            if (!message) {
                return;
            }

            const confirmed =
                window.confirm(message);

            if (!confirmed) {
                event.preventDefault();
                event.stopImmediatePropagation();
            }
        },
        true
    );
})();
