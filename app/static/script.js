// -----------------------------------------------------------
// GPS IMAGE WATERMARKING — stamps the live camera photo with
// location + timestamp before it's ever submitted, using the
// Canvas API. Runs entirely client-side.
// -----------------------------------------------------------

function stampImageWithLocation(file) {

    return new Promise(function (resolve) {

        if (!navigator.geolocation) {
            resolve(file);
            return;
        }

        navigator.geolocation.getCurrentPosition(

            function (position) {

                const lat = position.coords.latitude.toFixed(6);
                const lon = position.coords.longitude.toFixed(6);

                const timestamp = new Date().toLocaleString("en-IN", {
                    timeZone: "Asia/Kolkata",
                    dateStyle: "medium",
                    timeStyle: "short"
                });

                const reader = new FileReader();

                reader.onload = function (readerEvent) {

                    const img = new Image();

                    img.onload = function () {

                        const canvas = document.createElement("canvas");
                        canvas.width = img.width;
                        canvas.height = img.height;

                        const ctx = canvas.getContext("2d");
                        ctx.drawImage(img, 0, 0);

                        const bannerHeight = Math.max(
                            40,
                            Math.round(img.height * 0.08)
                        );

                        ctx.fillStyle = "rgba(0, 74, 42, 0.75)";
                        ctx.fillRect(
                            0,
                            img.height - bannerHeight,
                            img.width,
                            bannerHeight
                        );

                        const fontSize = Math.max(
                            14,
                            Math.round(img.width * 0.022)
                        );

                        ctx.fillStyle = "white";
                        ctx.font = `${fontSize}px sans-serif`;
                        ctx.textBaseline = "middle";

                        const label = (
                            `Location: Pauri Garhwal | Lat: ${lat} | ` +
                            `Lon: ${lon} | Time: ${timestamp}`
                        );

                        ctx.fillText(
                            label,
                            14,
                            img.height - bannerHeight / 2,
                            img.width - 28
                        );

                        canvas.toBlob(
                            function (blob) {
                                const stampedFile = new File(
                                    [blob],
                                    file.name.replace(/\.[^.]+$/, "") + ".jpg",
                                    { type: "image/jpeg" }
                                );
                                resolve(stampedFile);
                            },
                            "image/jpeg",
                            0.9
                        );
                    };

                    img.src = readerEvent.target.result;
                };

                reader.readAsDataURL(file);

            },

            function () {
                // Location denied/unavailable — submit the original,
                // un-stamped photo rather than blocking the report.
                resolve(file);
            },

            { enableHighAccuracy: true, timeout: 10000 }
        );

    });
}


document.addEventListener("DOMContentLoaded", function () {

    const reportForm = document.getElementById("report-form");

    if (!reportForm) {
        return;
    }

    reportForm.addEventListener("submit", async function (event) {

        event.preventDefault();

        const imageInput = reportForm.querySelector('input[name="image"]');
        const submitButton = reportForm.querySelector('button[type="submit"]');
        const originalButtonText = submitButton.textContent;

        if (!imageInput.files || !imageInput.files[0]) {
            alert("Please take a photo first.");
            return;
        }

        submitButton.disabled = true;
        submitButton.textContent = "📍 Stamping location...";

        try {

            const stampedFile = await stampImageWithLocation(
                imageInput.files[0]
            );

            const formData = new FormData(reportForm);
            formData.set("image", stampedFile);

            submitButton.textContent = "🚀 Submitting...";

            await fetch("/submit", {
                method: "POST",
                body: formData
            });

            window.location.href = "/dashboard";

        } catch (error) {

            alert(
                "Something went wrong submitting your report. " +
                "Please try again."
            );

            submitButton.disabled = false;
            submitButton.textContent = originalButtonText;
        }

    });

});


// -----------------------------------------------------------
// COMPLAINT CATEGORY — multi-tier dropdown matching national
// Swachhata compliance parameters. Level 2 options depend on
// whichever Level 1 department is chosen.
// -----------------------------------------------------------

const COMPLAINT_CATEGORIES = {
    "Solid Waste & Street Cleaning": [
        "Garbage Dump",
        "Dustbins Not Cleaned",
        "Sweeping Not Done",
        "Garbage Vehicle Not Arrived",
        "Burning of Garbage",
        "Cleanliness Target Unit"
    ],
    "Public Health & Toilet Maintenance": [
        "Uncleaning Public Toilet",
        "Blockage in Public Toilet",
        "No Water Supply",
        "No Electricity"
    ],
    "Civil Works & Drainage Infrastructure": [
        "Open Manholes or Drains",
        "Overflow of Sewerage",
        "Overflow of Septic Tanks",
        "Stagnant Water on Road"
    ],
    "Emergency & Special Operations": [
        "Removal of Debris",
        "Removal of Dead Animals",
        "Yellow Spot",
        "Open Defecation"
    ]
};

document.addEventListener("DOMContentLoaded", function () {

    const level1 = document.getElementById("category-level1");
    const level2 = document.getElementById("category-level2");

    if (!level1 || !level2) {
        return;
    }

    Object.keys(COMPLAINT_CATEGORIES).forEach(function (department) {
        const option = document.createElement("option");
        option.value = department;
        option.textContent = department;
        level1.appendChild(option);
    });

    level1.addEventListener("change", function () {

        const subcategories = COMPLAINT_CATEGORIES[level1.value] || [];

        level2.innerHTML = "";

        const placeholder = document.createElement("option");
        placeholder.value = "";
        placeholder.disabled = true;
        placeholder.selected = true;
        placeholder.textContent = "Choose a specific issue...";
        level2.appendChild(placeholder);

        subcategories.forEach(function (subcategory) {
            const option = document.createElement("option");
            option.value = subcategory;
            option.textContent = subcategory;
            level2.appendChild(option);
        });

        level2.disabled = subcategories.length === 0;
    });

});


// -----------------------------------------------------------
// SHOW/HIDE PASSWORD — wires up every ".toggle-password" button
// on the page (login, register, admin login all reuse this).
// -----------------------------------------------------------

document.addEventListener("DOMContentLoaded", function () {

    document.querySelectorAll(".toggle-password").forEach(function (button) {

        button.addEventListener("click", function () {

            const targetId = button.getAttribute("data-target");

            const input = document.getElementById(targetId);

            if (!input) {

                return;
            }

            const isHidden = input.type === "password";

            input.type = isHidden ? "text" : "password";

            button.textContent = isHidden ? "🙈" : "👁️";

            button.setAttribute(
                "aria-label",
                isHidden ? "Hide password" : "Show password"
            );

        });

    });

});


let map = null;
let marker = null;


function createMap(latitude, longitude) {

    if (map === null) {

        map = L.map("map").setView(
            [latitude, longitude],
            17
        );


        L.tileLayer(
            "https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png",
            {
                maxZoom: 19,

                attribution:
                    "&copy; OpenStreetMap contributors"
            }
        ).addTo(map);


        marker = L.marker(
            [latitude, longitude],
            {
                draggable: true
            }
        ).addTo(map);


        marker.on(
            "dragend",
            function(event) {

                const position =
                    event.target.getLatLng();


                setLocation(
                    position.lat,
                    position.lng
                );

            }
        );


        map.on(
            "click",
            function(event) {

                setLocation(
                    event.latlng.lat,
                    event.latlng.lng
                );

            }
        );

    }

    else {

        map.setView(
            [latitude, longitude],
            17
        );


        marker.setLatLng(
            [latitude, longitude]
        );

    }

}


async function setLocation(
    latitude,
    longitude
) {

    document.getElementById(
        "coordinates"
    ).value =
        latitude + ", " + longitude;


    document.getElementById(
        "location"
    ).value =
        "Finding address...";


    document.getElementById(
        "location-status"
    ).textContent =
        "🔍 Finding address...";


    try {

        const response = await fetch(

            `https://nominatim.openstreetmap.org/reverse?format=jsonv2&lat=${latitude}&lon=${longitude}`

        );


        if (!response.ok) {

            throw new Error(
                "Address lookup failed"
            );

        }


        const data =
            await response.json();


        const address =
            data.display_name ||
            `${latitude}, ${longitude}`;


        document.getElementById(
            "location"
        ).value =
            address;


        document.getElementById(
            "address"
        ).value =
            address;


        document.getElementById(
            "location-status"
        ).textContent =
            "✅ Location selected. Drag the pin if needed.";

    }

    catch (error) {

        const fallback =
            `${latitude}, ${longitude}`;


        document.getElementById(
            "location"
        ).value =
            fallback;


        document.getElementById(
            "address"
        ).value =
            fallback;


        document.getElementById(
            "location-status"
        ).textContent =
            "📍 Location selected. Drag the pin if needed.";

    }

}


function getLocation() {

    const status =
        document.getElementById(
            "location-status"
        );


    if (!navigator.geolocation) {

        status.textContent =
            "❌ Location is not supported by this browser.";

        return;
    }


    status.textContent =
        "📍 Finding your location...";


    navigator.geolocation.getCurrentPosition(

        function(position) {

            const latitude =
                position.coords.latitude;


            const longitude =
                position.coords.longitude;


            createMap(
                latitude,
                longitude
            );


            setLocation(
                latitude,
                longitude
            );

        },


        function(error) {

            status.textContent =
                "❌ Could not get your location. You can try again.";

        },


        {

            enableHighAccuracy: true,

            timeout: 15000,

            maximumAge: 0

        }

    );

}