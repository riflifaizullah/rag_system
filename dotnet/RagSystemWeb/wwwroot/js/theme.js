// Phase 5 theme toggle. The initial theme is already applied synchronously
// by the inline script in _Layout.cshtml (before Blazor even connects) --
// this file only handles the interactive toggle and reports the current
// value back to Blazor via JS interop.
window.stkTheme = {
    get: function () {
        try { return localStorage.getItem('stk-theme') || 'dark'; } catch (e) { return 'dark'; }
    },
    set: function (theme) {
        try { localStorage.setItem('stk-theme', theme); } catch (e) { }
        document.documentElement.setAttribute('data-theme', theme);
    }
};
