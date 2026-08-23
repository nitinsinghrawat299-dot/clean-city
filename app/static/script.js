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