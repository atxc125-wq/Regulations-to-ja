/**
 * viewer.js  --  3ペインビューワの操作ロジック
 */

(function () {
  'use strict';

  // ---------- アコーディオン ----------

  window.toggleParagraph = function (uid) {
    const card = document.getElementById('para-' + uid);
    const body = document.getElementById('body-' + uid);
    if (!card || !body) return;

    const isExpanded = !body.hidden;
    body.hidden = isExpanded;
    card.classList.toggle('expanded', !isExpanded);

    if (!isExpanded) {
      card.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    }
  };

  // ---------- 中ペイン: 段落ナビをクリックでスクロール ----------

  window.selectParagraph = function (uid) {
    const target = document.getElementById('para-' + uid);
    if (target) {
      target.scrollIntoView({ behavior: 'smooth', block: 'start' });
    }
    setActiveNavItem(uid);
  };

  function setActiveNavItem(uid) {
    document.querySelectorAll('.para-nav-item').forEach(function (el) {
      el.classList.toggle('active', el.dataset.uid === uid);
    });
    // ロックされていなければ中ペインをスクロール
    if (!lockedUid) {
      scrollMiddlePaneTo(uid);
    }
  }

  function scrollMiddlePaneTo(uid) {
    const item = document.querySelector('.para-nav-item[data-uid="' + uid + '"]');
    const pane = document.getElementById('pane-paragraphs');
    if (!item || !pane) return;
    const paneRect = pane.getBoundingClientRect();
    const itemRect = item.getBoundingClientRect();
    // アイテムがペインの表示範囲外なら中央へスクロール
    if (itemRect.top < paneRect.top || itemRect.bottom > paneRect.bottom) {
      item.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    }
  }

  // ---------- 左ペイン: 章クリック → 中ペイン絞り込み ----------

  window.selectChapter = function (chapterNumber) {
    const allItems = document.querySelectorAll('.para-nav-item');
    allItems.forEach(function (el) {
      const parent = el.dataset.parent || '';
      const uid = el.dataset.uid;
      const card = document.getElementById('para-' + uid);

      const isChild = parent === chapterNumber || parent.startsWith(chapterNumber + '.');
      const isSelf = card && card.dataset.number === chapterNumber;

      el.style.display = (isChild || isSelf) ? '' : 'none';
    });

    document.querySelectorAll('#chapter-tree .tree-btn').forEach(function (btn) {
      const item = btn.closest('.tree-item');
      btn.classList.toggle('active', item && item.dataset.number === chapterNumber);
    });

    // ロック解除して章頭へ
    unlockMiddlePane();

    const firstVisible = document.querySelector('.para-card[data-number="' + chapterNumber + '"]')
                      || document.querySelector('.para-card[data-parent="' + chapterNumber + '"]');
    if (firstVisible) {
      firstVisible.scrollIntoView({ behavior: 'smooth', block: 'start' });
    }
  };

  // ---------- 変更理由ポップアップ ----------

  window.showJustification = function (uid) {
    const box = document.getElementById('justification-' + uid);
    const body = document.getElementById('body-' + uid);
    if (!box) return;
    if (body && body.hidden) {
      window.toggleParagraph(uid);
    }
    box.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    box.style.outline = '2px solid #e9a80b';
    setTimeout(function () { box.style.outline = ''; }, 1500);
  };

  // ---------- ロック機能 ----------

  var lockedUid = null;

  window.toggleLock = function (uid, btn) {
    if (lockedUid === uid) {
      unlockMiddlePane();
    } else {
      lockMiddlePaneTo(uid, btn);
    }
  };

  function lockMiddlePaneTo(uid, btn) {
    // 既存のロックを解除
    unlockMiddlePane();

    lockedUid = uid;

    // ボタンとリストアイテムをロック状態に
    const item = document.querySelector('.para-nav-item[data-uid="' + uid + '"]');
    if (item) {
      item.classList.add('nav-locked');
      const lockBtn = item.querySelector('.lock-btn');
      if (lockBtn) {
        lockBtn.classList.add('lock-btn--active');
        lockBtn.title = 'ロック解除';
        lockBtn.setAttribute('aria-label', 'ロック解除');
      }
      // ロックされたアイテムを中ペインの上部に固定表示
      item.scrollIntoView({ behavior: 'smooth', block: 'start' });
    }

    const hint = document.getElementById('lock-hint');
    if (hint) hint.textContent = '🔒 ロック中';
  }

  function unlockMiddlePane() {
    if (!lockedUid) return;
    const item = document.querySelector('.para-nav-item[data-uid="' + lockedUid + '"]');
    if (item) {
      item.classList.remove('nav-locked');
      const lockBtn = item.querySelector('.lock-btn');
      if (lockBtn) {
        lockBtn.classList.remove('lock-btn--active');
        lockBtn.title = 'この段落でスクロールをロック';
        lockBtn.setAttribute('aria-label', 'この位置にロック');
      }
    }
    lockedUid = null;
    const hint = document.getElementById('lock-hint');
    if (hint) hint.textContent = '';
  }

  // ---------- 右ペインスクロール → 中ペイン自動追従 ----------

  function setupScrollSync() {
    const mainPane = document.getElementById('pane-content');
    if (!mainPane || !window.IntersectionObserver) return;

    var activeUid = null;

    const observer = new IntersectionObserver(function (entries) {
      // 最も上にある表示中の段落を探す
      var topEntry = null;
      entries.forEach(function (e) {
        if (e.isIntersecting) {
          if (!topEntry || e.boundingClientRect.top < topEntry.boundingClientRect.top) {
            topEntry = e;
          }
        }
      });

      if (topEntry) {
        var uid = topEntry.target.dataset.uid;
        if (uid && uid !== activeUid) {
          activeUid = uid;
          setActiveNavItem(uid);
        }
      }
    }, {
      root: mainPane,
      rootMargin: '0px 0px -60% 0px',
      threshold: 0
    });

    document.querySelectorAll('.para-card[data-uid]').forEach(function (card) {
      observer.observe(card);
    });
  }

  // ---------- グロッサリー ツールチップ ----------

  const tooltip = document.getElementById('glossary-tooltip');

  function showTooltip(event) {
    const el = event.currentTarget;
    const term = el.dataset.term;
    const definition = el.dataset.definition;
    const ref = el.dataset.ref;

    if (!tooltip) return;
    tooltip.innerHTML =
      '<div class="tooltip-term">' + escapeHtml(term) + '</div>' +
      '<div class="tooltip-def">' + escapeHtml(definition) + '</div>' +
      (ref ? '<div class="tooltip-ref">定義: § ' + escapeHtml(ref) + '</div>' : '');

    tooltip.classList.add('visible');
    positionTooltip(event);
  }

  function moveTooltip(event) {
    positionTooltip(event);
  }

  function hideTooltip() {
    if (tooltip) tooltip.classList.remove('visible');
  }

  function positionTooltip(event) {
    if (!tooltip) return;
    const margin = 12;
    const tw = tooltip.offsetWidth;
    const th = tooltip.offsetHeight;
    let x = event.clientX + margin;
    let y = event.clientY + margin;

    if (x + tw > window.innerWidth - margin) {
      x = event.clientX - tw - margin;
    }
    if (y + th > window.innerHeight - margin) {
      y = event.clientY - th - margin;
    }
    tooltip.style.left = x + 'px';
    tooltip.style.top  = y + 'px';
  }

  function escapeHtml(str) {
    return (str || '').replace(/[&<>"']/g, function (c) {
      return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c];
    });
  }

  function attachGlossaryListeners() {
    document.querySelectorAll('.glossary-term').forEach(function (el) {
      el.addEventListener('mouseenter', showTooltip);
      el.addEventListener('mousemove',  moveTooltip);
      el.addEventListener('mouseleave', hideTooltip);
      el.addEventListener('focus',      showTooltip);
      el.addEventListener('blur',       hideTooltip);
    });
  }

  // ---------- 初期化 ----------

  document.addEventListener('DOMContentLoaded', function () {
    attachGlossaryListeners();
    setupScrollSync();

    const observer = new MutationObserver(function (mutations) {
      mutations.forEach(function (m) {
        m.addedNodes.forEach(function (node) {
          if (node.nodeType !== 1) return;
          node.querySelectorAll && node.querySelectorAll('.glossary-term').forEach(function (el) {
            el.addEventListener('mouseenter', showTooltip);
            el.addEventListener('mousemove',  moveTooltip);
            el.addEventListener('mouseleave', hideTooltip);
          });
        });
      });
    });
    observer.observe(document.body, { childList: true, subtree: true });

    if (location.hash && location.hash.startsWith('#para-')) {
      const uid = location.hash.slice('#para-'.length);
      const target = document.getElementById('para-' + uid);
      if (target) {
        setTimeout(function () {
          target.scrollIntoView({ behavior: 'smooth', block: 'start' });
          window.toggleParagraph(uid);
        }, 300);
      }
    }
  });

})();
