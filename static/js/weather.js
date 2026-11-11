/* Live weather strip: Open-Meteo (free, no key). Default US city; choose another or use your location. */
(function () {
  var CITIES = [
    ["New York, US", 40.71, -74.01], ["Los Angeles, US", 34.05, -118.24], ["Chicago, US", 41.88, -87.63],
    ["Houston, US", 29.76, -95.37], ["Miami, US", 25.76, -80.19], ["Seattle, US", 47.61, -122.33],
    ["Dallas, US", 32.78, -96.8], ["Washington DC, US", 38.91, -77.04],
    ["London, UK", 51.51, -0.13], ["Manchester, UK", 53.48, -2.24], ["Edinburgh, UK", 55.95, -3.19],
    ["Toronto, Canada", 43.65, -79.38], ["Vancouver, Canada", 49.28, -123.12], ["Montreal, Canada", 45.5, -73.57],
    ["Sydney, Australia", -33.87, 151.21], ["Melbourne, Australia", -37.81, 144.96], ["Auckland, NZ", -36.85, 174.76],
    ["Dublin, Ireland", 53.35, -6.26], ["Paris, France", 48.86, 2.35], ["Berlin, Germany", 52.52, 13.4],
    ["Amsterdam, Netherlands", 52.37, 4.9], ["Madrid, Spain", 40.42, -3.7]
  ];
  var WMO = function (c, day) {
    if (c === 0) return [day ? "☀️" : "🌙", "Clear"];
    if (c <= 2) return [day ? "🌤️" : "☁️", "Partly cloudy"];
    if (c === 3) return ["☁️", "Overcast"];
    if (c === 45 || c === 48) return ["🌫️", "Fog"];
    if (c >= 51 && c <= 57) return ["🌦️", "Drizzle"];
    if (c >= 61 && c <= 67) return ["🌧️", "Rain"];
    if (c >= 71 && c <= 77) return ["❄️", "Snow"];
    if (c >= 80 && c <= 82) return ["🌦️", "Showers"];
    if (c === 85 || c === 86) return ["🌨️", "Snow showers"];
    if (c >= 95) return ["⛈️", "Thunderstorm"];
    return ["🌡️", "Weather"];
  };
  var box = document.getElementById("wx");
  if (!box) return;
  var sel = box.querySelector("select"), out = box.querySelector(".wx-now"), unitBtn = box.querySelector(".wx-unit"), locBtn = box.querySelector(".wx-loc");
  var st = { lat: 40.71, lon: -74.01, name: "New York, US", f: true };
  function get(k) { try { return localStorage.getItem(k); } catch (e) { return null; } }
  function set(k, v) { try { localStorage.setItem(k, v); } catch (e) {} }
  CITIES.forEach(function (c, i) { var o = document.createElement("option"); o.value = i; o.textContent = c[0]; sel.appendChild(o); });
  var saved = get("wx_city");
  if (saved === "geo") { try { var g = JSON.parse(get("wx_geo")); st.lat = g.lat; st.lon = g.lon; st.name = "Your location"; addGeo(); sel.value = "geo"; } catch (e) {} }
  else if (saved !== null && CITIES[+saved]) { var c = CITIES[+saved]; st.lat = c[1]; st.lon = c[2]; st.name = c[0]; sel.value = saved; }
  else { sel.value = "0"; }
  var u = get("wx_unit"); st.f = u ? u === "f" : (st.name.slice(-2) === "US");
  function addGeo() { if (!sel.querySelector('option[value="geo"]')) { var o = document.createElement("option"); o.value = "geo"; o.textContent = "Your location"; sel.insertBefore(o, sel.firstChild); } }
  function fmt(t) { return Math.round(t) + "°" + (st.f ? "F" : "C"); }
  function load() {
    unitBtn.textContent = st.f ? "°F" : "°C";
    var url = "https://api.open-meteo.com/v1/forecast?latitude=" + st.lat + "&longitude=" + st.lon +
      "&current=temperature_2m,apparent_temperature,weather_code,wind_speed_10m,is_day&daily=temperature_2m_max,temperature_2m_min" +
      "&timezone=auto&forecast_days=1&temperature_unit=" + (st.f ? "fahrenheit" : "celsius") + "&wind_speed_unit=" + (st.f ? "mph" : "kmh");
    fetch(url).then(function (r) { return r.json(); }).then(function (d) {
      var c = d.current, w = WMO(c.weather_code, c.is_day === 1);
      out.innerHTML = "<span class='wx-ic' aria-hidden='true'>" + w[0] + "</span><b>" + fmt(c.temperature_2m) + "</b> " + w[1] +
        " <span class='wx-sub'>H " + fmt(d.daily.temperature_2m_max[0]) + " · L " + fmt(d.daily.temperature_2m_min[0]) +
        " · Feels " + fmt(c.apparent_temperature) + " · Wind " + Math.round(c.wind_speed_10m) + (st.f ? " mph" : " km/h") + "</span>";
      try { window.WXSTATE = st; document.dispatchEvent(new CustomEvent("wx:update", { detail: st })); } catch (e) {}
    }).catch(function () { out.textContent = "Weather unavailable right now"; });
  }
  sel.addEventListener("change", function () {
    if (sel.value === "geo") return;
    var c = CITIES[+sel.value]; st.lat = c[1]; st.lon = c[2]; st.name = c[0]; set("wx_city", sel.value);
    if (!get("wx_unit")) st.f = c[0].slice(-2) === "US"; load();
  });
  unitBtn.addEventListener("click", function () { st.f = !st.f; set("wx_unit", st.f ? "f" : "c"); load(); });
  locBtn.addEventListener("click", function () {
    if (!navigator.geolocation) { out.textContent = "Location not supported"; return; }
    navigator.geolocation.getCurrentPosition(function (p) {
      st.lat = Math.round(p.coords.latitude * 100) / 100; st.lon = Math.round(p.coords.longitude * 100) / 100; st.name = "Your location";
      addGeo(); sel.value = "geo"; set("wx_city", "geo"); set("wx_geo", JSON.stringify({ lat: st.lat, lon: st.lon })); load();
    }, function () { out.textContent = "Location blocked — pick a city instead"; }, { timeout: 8000 });
  });
  load();
})();
