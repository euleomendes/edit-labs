// The editor is written in Portuguese. For English or Spanish this translates the page as it renders:
// every text node and title/placeholder is looked up in I18N_DICT (exact match first, then known phrases inside it).
(function(){
  const LANGS = {pt:'Português', en:'English', es:'Español'};
  let lang = 'pt'; try{ lang = localStorage.getItem('vb-lang') || 'pt'; }catch(e){}
  if (!LANGS[lang]) lang = 'pt';
  document.documentElement.lang = lang==='pt'? 'pt-BR' : lang;
  const D = window.I18N_DICT || {en:{}, es:{}};
  // Numbers and plurals that are built on the fly.
  const RULES = [
    [/^(\d+) alteraç(?:ões|ão)$/, n=>({en: n+' change'+(n>1?'s':''), es: n+' cambio'+(n>1?'s':'')})],
    [/^(\d+) linhas? cortadas automaticamente$/, n=>({en: n+' line'+(n>1?'s':'')+' cut automatically', es: n+' línea'+(n>1?'s':'')+' cortada'+(n>1?'s':'')+' automáticamente'})],
    [/^(\d+) linhas? cortadas$/, n=>({en: n+' line'+(n>1?'s':'')+' cut', es: n+' línea'+(n>1?'s':'')+' cortada'+(n>1?'s':'')})],
    [/^(\d+) pausas · ([\d.]+)s economizados$/, (n,s)=>({en: n+' pauses · '+s+'s saved', es: n+' pausas · '+s+'s ahorrados'})],
    [/^(\d+) linhas · (\d+) palavras([\s\S]*)$/, (a,b,r)=>({en: a+' lines · '+b+' words'+r, es: a+' líneas · '+b+' palabras'+r})],
    [/^(\d+) linhas?$/, n=>({en: n+' line'+(n>1?'s':''), es: n+' línea'+(n>1?'s':'')})],
    [/^Rosto (\d+)$/, n=>({en: 'Face '+n, es: 'Rostro '+n})],
    [/^(Todos|Gráficos|Vídeos|Fotos|GIFs) (\d+)$/, (w,n)=>({en: ({Todos:'All',Gráficos:'Graphics',Vídeos:'Videos',Fotos:'Photos',GIFs:'GIFs'})[w]+' '+n, es: ({Todos:'Todos',Gráficos:'Gráficos',Vídeos:'Videos',Fotos:'Fotos',GIFs:'GIFs'})[w]+' '+n})],
    [/^(\d+) textos?$/, n=>({en: n+' text'+(n>1?'s':''), es: n+' texto'+(n>1?'s':'')})],
    [/^(\d+) palavras?$/, n=>({en: n+' word'+(n>1?'s':''), es: n+' palabra'+(n>1?'s':'')})],
    [/^(\d+) sons?$/, n=>({en: n+' sound'+(n>1?'s':''), es: n+' sonido'+(n>1?'s':'')})],
  ];
  let phraseRe = null, phraseMap = null;
  function buildPhrases(){
    phraseMap = D[lang] || {};
    // Phrases long enough to be safe inside a longer text (short words only match exactly).
    const keys = Object.keys(phraseMap).filter(k=>/\s/.test(k.trim()) || /[“”(:—·]/.test(k)).sort((a,b)=>b.length-a.length);
    if (!keys.length){ phraseRe = null; return; }
    const esc = s=>s.replace(/[.*+?^${}()|[\]\\]/g,'\\$&');
    const L = /[\p{L}\p{N}]/u;
    phraseRe = new RegExp(keys.map(k=>(L.test(k[0])?'(?<![\\p{L}\\p{N}])':'')+esc(k)+(L.test(k[k.length-1])?'(?![\\p{L}\\p{N}])':'')).join('|'), 'gu');
  }
  function tr(s){
    if (lang==='pt' || !s) return s;
    const m = s.match(/^(\s*)([\s\S]*?)(\s*)$/); const core = m[2];
    if (!core || !/\p{L}/u.test(core)) return s;
    const map = D[lang];
    if (map[core] != null) return m[1]+map[core]+m[3];
    for (const [re, fn] of RULES){ const r = core.match(re); if (r) return m[1]+fn(...r.slice(1))[lang]+m[3]; }
    if (!phraseRe) return s;
    phraseRe.lastIndex = 0;
    return phraseRe.test(core) ? m[1]+core.replace(phraseRe, k=>map[k]!=null? map[k] : k)+m[3] : s;
  }
  window.T = tr; window.I18N_LANG = lang; window.I18N_LANGS = LANGS;
  // Original Portuguese of every node and attribute we changed, so switching language never loses it.
  const origText = new WeakMap(), doneText = new WeakMap();
  const ATTRS = ['title','placeholder','aria-label'];
  const SKIP = 'script,style,textarea,canvas,.w,[data-noi18n]';
  function doText(n){
    const p = n.parentElement; if (!p || p.closest(SKIP)) return;
    const cur = n.nodeValue; if (doneText.get(n) === cur) return;
    origText.set(n, cur); const t = tr(cur); doneText.set(n, t); if (t !== cur) n.nodeValue = t;
  }
  function doEl(el){
    if (el.closest && el.closest('script,style,[data-noi18n]')) return;
    for (const a of ATTRS){ if (!el.hasAttribute(a)) continue; const v = el.getAttribute(a); const k = 'data-i18n-'+a;
      if (el.getAttribute(k+'-t') === v) continue; el.setAttribute(k, v); const t = tr(v); el.setAttribute(k+'-t', t); if (t !== v) el.setAttribute(a, t); }
  }
  function walk(root){
    if (root.nodeType === 3){ doText(root); return; }
    if (root.nodeType !== 1) return;
    doEl(root);
    if (root.matches && root.matches(SKIP)) return;
    const tw = document.createTreeWalker(root, NodeFilter.SHOW_TEXT|NodeFilter.SHOW_ELEMENT, {acceptNode: x=> x.nodeType===1 && x.matches(SKIP) ? NodeFilter.FILTER_REJECT : NodeFilter.FILTER_ACCEPT});
    let x; while ((x = tw.nextNode())){ if (x.nodeType===3) doText(x); else doEl(x); }
    root.querySelectorAll && root.querySelectorAll('textarea[placeholder],input[placeholder]').forEach(doEl);
  }
  let obs = null;
  function start(){
    if (lang==='pt') return;
    buildPhrases(); walk(document.body);
    obs = new MutationObserver(ms=>{ for (const m of ms){
      if (m.type==='characterData') doText(m.target);
      else if (m.type==='attributes') doEl(m.target);
      else m.addedNodes.forEach(walk);
    } });
    obs.observe(document.body, {subtree:true, childList:true, characterData:true, attributes:true, attributeFilter:ATTRS});
  }
  window.setLang = function(nx){
    if (!LANGS[nx] || nx===lang) return;
    try{ localStorage.setItem('vb-lang', nx); }catch(e){}
    if (obs){ obs.disconnect(); obs = null; }
    // put the Portuguese back, then translate again into the new language
    const tw = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT); let x;
    while ((x = tw.nextNode())){ if (origText.has(x) && doneText.get(x) === x.nodeValue){ x.nodeValue = origText.get(x); } doneText.delete(x); }
    document.querySelectorAll('[data-i18n-title],[data-i18n-placeholder],[data-i18n-aria-label]').forEach(el=>{ for (const a of ATTRS){ const k='data-i18n-'+a; if (el.hasAttribute(k)){ if (el.getAttribute(k+'-t')===el.getAttribute(a)) el.setAttribute(a, el.getAttribute(k)); el.removeAttribute(k); el.removeAttribute(k+'-t'); } } });
    lang = nx; window.I18N_LANG = lang; document.documentElement.lang = lang==='pt'? 'pt-BR' : lang;
    start(); if (typeof window.onLangChange === 'function') window.onLangChange(lang);
  };
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start); else start();
})();
