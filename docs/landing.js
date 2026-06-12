/* Scout landing — interaction layer (vanilla, progressive enhancement) */
(function () {
  var d = document, root = d.documentElement;
  root.classList.add('js');
  var reduce = window.matchMedia && matchMedia('(prefers-reduced-motion: reduce)').matches;
  var hasIO = 'IntersectionObserver' in window;

  /* stagger: assign incremental --d delay to children of .stagger */
  [].forEach.call(d.querySelectorAll('.stagger'), function (group) {
    [].forEach.call(group.children, function (c, i) {
      if (!c.style.getPropertyValue('--d')) c.style.setProperty('--d', (i * 0.08).toFixed(2) + 's');
    });
  });

  /* reveal on scroll */
  var revs = [].slice.call(d.querySelectorAll('.reveal, .reveal-l, .timeline'));
  if (reduce || !hasIO) {
    revs.forEach(function (e) { e.classList.add('is-in'); });
  } else {
    var io = new IntersectionObserver(function (entries) {
      entries.forEach(function (en) {
        if (en.isIntersecting) { en.target.classList.add('is-in'); io.unobserve(en.target); }
      });
    }, { threshold: 0.14, rootMargin: '0px 0px -7% 0px' });
    revs.forEach(function (e) { io.observe(e); });
  }

  /* count-up stats */
  function countUp(el) {
    var target = parseFloat(el.getAttribute('data-count')) || 0;
    var suffix = el.getAttribute('data-suffix') || '';
    if (reduce) { el.textContent = target + suffix; return; }
    var start = null, dur = 1100;
    function frame(ts) {
      if (start === null) start = ts;
      var p = Math.min((ts - start) / dur, 1);
      var eased = 1 - Math.pow(1 - p, 3);
      el.textContent = Math.round(target * eased) + suffix;
      if (p < 1) requestAnimationFrame(frame);
    }
    requestAnimationFrame(frame);
  }
  var counts = [].slice.call(d.querySelectorAll('[data-count]'));
  if (hasIO && !reduce) {
    var cio = new IntersectionObserver(function (entries) {
      entries.forEach(function (en) {
        if (en.isIntersecting) { countUp(en.target); cio.unobserve(en.target); }
      });
    }, { threshold: 0.6 });
    counts.forEach(function (e) { cio.observe(e); });
  } else {
    counts.forEach(function (e) { e.textContent = (e.getAttribute('data-count') || '') + (e.getAttribute('data-suffix') || ''); });
  }

  /* active nav link by section */
  var links = [].slice.call(d.querySelectorAll('.nav-links a.plain'));
  var byId = {};
  links.forEach(function (a) { var id = (a.getAttribute('href') || '').slice(1); if (id) byId[id] = a; });
  var secs = Object.keys(byId).map(function (id) { return d.getElementById(id); }).filter(Boolean);
  if (hasIO && secs.length) {
    var nio = new IntersectionObserver(function (entries) {
      entries.forEach(function (en) {
        if (en.isIntersecting) {
          links.forEach(function (l) { l.classList.remove('active'); });
          var a = byId[en.target.id]; if (a) a.classList.add('active');
        }
      });
    }, { rootMargin: '-45% 0px -50% 0px' });
    secs.forEach(function (s) { nio.observe(s); });
  }

  /* scroll progress bar */
  var bar = d.querySelector('.scroll-progress');
  if (bar) {
    var onScroll = function () {
      var h = d.documentElement;
      var max = h.scrollHeight - h.clientHeight;
      bar.style.width = (max > 0 ? (h.scrollTop / max) * 100 : 0) + '%';
    };
    addEventListener('scroll', onScroll, { passive: true });
    addEventListener('resize', onScroll, { passive: true });
    onScroll();
  }
})();
