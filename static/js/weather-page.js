/* Weather page: 7-day forecast + optional on-device severe-weather alerts (Open-Meteo, no key). */
(function () {
  var days = document.getElementById("wp-days"), cityEl = document.getElementById("wp-city"), warn = document.getElementById("wp-warn");
  var btn = document.getElementById("wp-notify"), msg = document.getElementById("wp-notify-msg");
  if (!days) return;
  function get(k) { try { return localStorage.getItem(k); } catch (e) { return null; } }
  function set(k, v) { try { localStorage.setItem(k, v); } catch (e) {} }
  var ICON = function (c) { return c === 0 ? "☀️" : c <= 2 ? "🌤️" : c === 3 ? "☁️" : c <= 48 ? "🌫️" : c <= 57 ? "🌦️" : c <= 67 ? "🌧️" : c <= 77 ? "❄️" : c <= 82 ? "🌦️" : c <= 86 ? "🌨️" : "⛈️"; };
  var last = null;

  function severe(d, f) { /* returns text for the first severe day in the next 2 days, or "" */
    for (var i = 0; i < 2 && i < d.time.length; i++) {
      var c = d.weather_code[i], hi = d.temperature_2m_max[i], lo = d.temperature_2m_min[i];
      var hiC = f ? (hi - 32) * 5 / 9 : hi, loC = f ? (lo - 32) * 5 / 9 : lo, when = i === 0 ? "today" : "tomorrow";
      if (c >= 95) return "Thunderstorms expected " + when;
      if (c === 75 || c === 86 || c === 67 || c === 65 && d.precipitation_probability_max[i] >= 80) return "Heavy " + (c >= 71 ? "snow" : "rain") + " expected " + when;
      if (hiC >= 35) return "Extreme heat expected " + when + " (" + Math.round(hi) + "°)";
      if (loC <= -15) return "Dangerous cold expected " + when + " (" + Math.round(lo) + "°)";
      if (d.wind_speed_10m_max[i] >= (f ? 45 : 70)) return "Strong winds expected " + when;
    }
    return "";
  }

  function render(st) {
    last = st; cityEl.textContent = "· " + st.name;
    var url = "https://api.open-meteo.com/v1/forecast?latitude=" + st.lat + "&longitude=" + st.lon +
      "&daily=weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max,wind_speed_10m_max&timezone=auto&forecast_days=7" +
      "&temperature_unit=" + (st.f ? "fahrenheit" : "celsius") + "&wind_speed_unit=" + (st.f ? "mph" : "kmh");
    fetch(url).then(function (r) { return r.json(); }).then(function (r) {
      var d = r.daily, html = "";
      for (var i = 0; i < d.time.length; i++) {
        var dt = new Date(d.time[i] + "T12:00:00"), nm = i === 0 ? "Today" : dt.toLocaleDateString("en", { weekday: "short" });
        html += "<div class='wp-day'><b>" + nm + "</b><span class='wp-ic'>" + ICON(d.weather_code[i]) + "</span><span>" + Math.round(d.temperature_2m_max[i]) +
          "° <i>" + Math.round(d.temperature_2m_min[i]) + "°</i></span><small>💧 " + (d.precipitation_probability_max[i] || 0) + "%</small></div>";
      }
      days.innerHTML = html;
      var s = severe(d, st.f);
      warn.hidden = !s; warn.textContent = s ? "⚠️ " + s + " in " + st.name + "." : "";
      if (s && get("wx_notify") === "on" && "Notification" in window && Notification.permission === "granted") {
        var key = "wx_last_alert", today = new Date().toISOString().slice(0, 10) + s;
        if (get(key) !== today) { set(key, today); try { new Notification("Weather alert – " + st.name, { body: s }); } catch (e) {} }
      }
    }).catch(function () { days.innerHTML = "<p>Forecast unavailable right now.</p>"; });
  }

  document.addEventListener("wx:update", function (e) { render(e.detail); });
  if (window.WXSTATE) render(window.WXSTATE);

  function sync() {
    if (!("Notification" in window)) { btn.hidden = true; msg.textContent = "Your browser doesn't support alerts. Use email, Telegram or Facebook instead."; return; }
    var on = get("wx_notify") === "on" && Notification.permission === "granted";
    btn.textContent = on ? "Alerts are ON (turn off)" : "Turn on alerts";
    if (Notification.permission === "denied") msg.textContent = "Alerts are blocked in your browser settings. Allow notifications for this site to use them.";
  }
  btn.addEventListener("click", function () {
    if (get("wx_notify") === "on") { set("wx_notify", "off"); sync(); return; }
    Notification.requestPermission().then(function (p) {
      if (p === "granted") { set("wx_notify", "on"); msg.textContent = "Done! You'll get a pop-up if severe weather is forecast for your city."; if (last) render(last); }
      sync();
    });
  });
  sync();
})();
