/**
 * viewer.js  --  3ペインビューワの操作ロジック
 */

(function () {
  'use strict';

  // ---------- アコーディオン ----------

  window.toggleParagraph = function (uid) {
    var card = document.getElementById('para-' + uid);
    var body = document.getElementById('body-' + uid);
    if (!card || !body) return;

    var isExpanded = !body.hidden;
    body.hidden = isExpanded;
    card.classList.toggle('expanded', !isExpanded);

    if (!isExpanded) {
      card.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    }
  };

  // ---------- 中ペイン: 段落ナビをクリックでスクロール ----------

  window.selectParagraph = function (uid) {
    var target = document.getElementById('para-' + uid);
    if (target) {
      target.scrollIntoView({ behavior: 'smooth', block: 'start' });
    }
    setActiveNavItem(uid);
    closeMobilePane();
  };

  function setActiveNavItem(uid) {
    document.querySelectorAll('.para-nav-item').forEach(function (el) {
      el.classList.toggle('active', el.dataset.uid === uid);
    });
    scrollMiddlePaneTo(uid);

    // ピン留め中でなければモバイルナビバーの段落行を更新
    if (!lockedUid) {
      updateMobileParaRow(uid);
    }
  }

  function scrollMiddlePaneTo(uid) {
    var item = document.querySelector('.para-nav-item[data-uid="' + uid + '"]');
    var pane = document.getElementById('pane-paragraphs');
    if (!item || !pane) return;
    var pinned = document.querySelector('.nav-pinned');
    var pinnedH = (pinned && pinned !== item) ? pinned.offsetHeight : 0;
    var paneRect = pane.getBoundingClientRect();
    var itemRect = item.getBoundingClientRect();
    var topBound = paneRect.top + pinnedH;
    if (itemRect.top < topBound || itemRect.bottom > paneRect.bottom) {
      item.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    }
  }

  // ---------- 左ペイン: 章クリック → 中ペイン絞り込み ----------

  window.selectChapter = function (chapterNumber) {
    var anyVisible = false;
    var allItems = document.querySelectorAll('.para-nav-item');
    allItems.forEach(function (el) {
      var parent = el.dataset.parent || '';
      var uid = el.dataset.uid;
      var card = document.getElementById('para-' + uid);

      var isChild = parent === chapterNumber || parent.startsWith(chapterNumber + '.');
      var isSelf = card && card.dataset.number === chapterNumber;
      var show = isChild || isSelf;

      el.style.display = show ? '' : 'none';
      if (show) anyVisible = true;
    });

    var placeholder = document.getElementById('para-nav-placeholder');
    if (placeholder) placeholder.style.display = anyVisible ? 'none' : '';

    document.querySelectorAll('#chapter-tree .tree-btn').forEach(function (btn) {
      var item = btn.closest('.tree-item');
      btn.classList.toggle('active', item && item.dataset.number === chapterNumber);
    });

    unlockMiddlePane();
    closeMobilePane();

    // モバイルナビバーの章行を更新
    var chapterItem = document.querySelector('#chapter-tree .tree-item[data-number="' + chapterNumber + '"]');
    if (chapterItem) {
      var jaEl = chapterItem.querySelector('.tree-title-ja');
      var enEl = chapterItem.querySelector('.tree-title-en');
      var title = (jaEl && jaEl.textContent.trim()) || (enEl && enEl.textContent.trim()) || '';
      updateMobileChapterRow(chapterNumber, title);
    }

    var firstVisible = document.querySelector('.para-card[data-number="' + chapterNumber + '"]')
                    || document.querySelector('.para-card[data-parent="' + chapterNumber + '"]');
    if (firstVisible) {
      firstVisible.scrollIntoView({ behavior: 'smooth', block: 'start' });
    }
  };

  // ---------- 変更理由ポップアップ ----------

  window.showJustification = function (uid) {
    var box = document.getElementById('justification-' + uid);
    var body = document.getElementById('body-' + uid);
    if (!box) return;
    if (body && body.hidden) {
      window.toggleParagraph(uid);
    }
    box.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    box.style.outline = '2px solid #e9a80b';
    setTimeout(function () { box.style.outline = ''; }, 1500);
  };

  // ---------- ピン留め機能（デスクトップ） ----------

  var lockedUid = null;

  window.toggleLock = function (uid) {
    if (lockedUid === uid) {
      unlockMiddlePane();
    } else {
      lockMiddlePaneTo(uid);
    }
  };

  function lockMiddlePaneTo(uid) {
    unlockMiddlePane();
    lockedUid = uid;

    var pane = document.getElementById('pane-paragraphs');
    var item = document.querySelector('.para-nav-item[data-uid="' + uid + '"]');
    if (item) {
      item.classList.add('nav-pinned');
      var header = pane && pane.querySelector('.pane-header');
      item.style.top = (header ? header.offsetHeight : 0) + 'px';
      var lockBtn = item.querySelector('.lock-btn');
      if (lockBtn) {
        lockBtn.classList.add('lock-btn--active');
        lockBtn.title = 'ピン留め解除';
        lockBtn.setAttribute('aria-label', 'ピン留め解除');
      }
      if (pane) {
        pane.scrollTop = item.offsetTop - pane.offsetTop;
      }
      // モバイルナビバーの段落行をピン留め項目で固定表示
      var numEl = item.querySelector('.tree-num');
      var jaEl  = item.querySelector('.tree-title-ja');
      var enEl  = item.querySelector('.tree-title-en');
      var title = (jaEl && jaEl.textContent.trim()) || (enEl && enEl.textContent.trim()) || '';
      updateMobileParaRow(null, (numEl ? numEl.textContent.trim() : ''), title, true);
    }

    var hint = document.getElementById('lock-hint');
    if (hint) hint.textContent = '📌 ピン留め中';
  }

  function unlockMiddlePane() {
    if (!lockedUid) return;
    var item = document.querySelector('.para-nav-item[data-uid="' + lockedUid + '"]');
    if (item) {
      item.classList.remove('nav-pinned');
      item.style.top = '';
      var lockBtn = item.querySelector('.lock-btn');
      if (lockBtn) {
        lockBtn.classList.remove('lock-btn--active');
        lockBtn.title = 'ここにピン留め';
        lockBtn.setAttribute('aria-label', 'ここにピン留め');
      }
    }
    lockedUid = null;
    var hint = document.getElementById('lock-hint');
    if (hint) hint.textContent = '';

    // モバイルナビバーのピン留め表示を解除
    var paraText = document.getElementById('mobile-para-text');
    if (paraText) paraText.removeAttribute('data-pinned');
  }

  // ---------- モバイルナビバー ----------

  function updateMobileChapterRow(num, title) {
    var el = document.getElementById('mobile-chapter-text');
    if (!el) return;
    el.textContent = num + (title ? ' ' + title : '');
  }

  function updateMobileParaRow(uid, num, title, pinned) {
    var el = document.getElementById('mobile-para-text');
    if (!el) return;
    // ピン留め中はUIDベースの自動更新を無視
    if (!pinned && el.dataset.pinned) return;

    var displayNum = num;
    var displayTitle = title;

    if (uid) {
      var item = document.querySelector('.para-nav-item[data-uid="' + uid + '"]');
      if (item) {
        var numEl  = item.querySelector('.tree-num');
        var jaEl   = item.querySelector('.tree-title-ja');
        var enEl   = item.querySelector('.tree-title-en');
        displayNum   = numEl  ? numEl.textContent.trim()  : '';
        displayTitle = (jaEl && jaEl.textContent.trim()) || (enEl && enEl.textContent.trim()) || '';
      }
    }

    el.textContent = displayNum + (displayTitle ? ' ' + displayTitle : '');
    if (pinned) {
      el.dataset.pinned = '1';
    } else {
      delete el.dataset.pinned;
    }
  }

  // ドロワー開閉
  window.toggleMobilePane = function (side) {
    var paneId = side === 'left' ? 'pane-chapters' : 'pane-paragraphs';
    var rowId  = side === 'left' ? 'mobile-chapter-row' : 'mobile-para-row';
    var pane   = document.getElementById(paneId);
    var row    = document.getElementById(rowId);
    var overlay = document.getElementById('mobile-overlay');

    if (!pane) return;

    var isOpen = pane.classList.contains('mobile-open');
    closeMobilePane();

    if (!isOpen) {
      // ドロワーを開く: モバイルナビバーの直下に表示
      var mobileNav = document.getElementById('mobile-nav');
      var navBottom = mobileNav ? (mobileNav.getBoundingClientRect().bottom + window.scrollY) : 0;
      pane.style.top = navBottom + 'px';
      pane.classList.add('mobile-open');
      if (row) row.setAttribute('aria-expanded', 'true');
      if (overlay) overlay.classList.add('visible');
    }
  };

  window.closeMobilePane = function () {
    document.querySelectorAll('.pane-left, .pane-middle').forEach(function (p) {
      p.classList.remove('mobile-open');
      p.style.top = '';
    });
    document.querySelectorAll('.mobile-nav-row').forEach(function (r) {
      r.setAttribute('aria-expanded', 'false');
    });
    var overlay = document.getElementById('mobile-overlay');
    if (overlay) overlay.classList.remove('visible');
  };

  // ---------- 右ペインスクロール → 中ペイン自動追従 ----------

  function setupScrollSync() {
    var mainPane = document.getElementById('pane-content');
    if (!mainPane || !window.IntersectionObserver) return;

    var activeUid = null;

    var observer = new IntersectionObserver(function (entries) {
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

  var tooltip = document.getElementById('glossary-tooltip');

  function showTooltip(event) {
    var el = event.currentTarget;
    var term = el.dataset.term;
    var definition = el.dataset.definition;
    var ref = el.dataset.ref;

    if (!tooltip) return;
    tooltip.innerHTML =
      '<div class="tooltip-term">' + escapeHtml(term) + '</div>' +
      '<div class="tooltip-def">' + escapeHtml(definition) + '</div>' +
      (ref ? '<div class="tooltip-ref">定義: § ' + escapeHtml(ref) + '</div>' : '');

    tooltip.classList.add('visible');
    positionTooltip(event);
  }

  function moveTooltip(event) { positionTooltip(event); }

  function hideTooltip() {
    if (tooltip) tooltip.classList.remove('visible');
  }

  function positionTooltip(event) {
    if (!tooltip) return;
    var margin = 12;
    var tw = tooltip.offsetWidth;
    var th = tooltip.offsetHeight;
    var x = event.clientX + margin;
    var y = event.clientY + margin;

    if (x + tw > window.innerWidth  - margin) x = event.clientX - tw - margin;
    if (y + th > window.innerHeight - margin) y = event.clientY - th - margin;
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

  // ---------- トップへ戻るボタン ----------

  function setupBackToTop() {
    var btn = document.getElementById('back-to-top');
    if (!btn) return;
    window.addEventListener('scroll', function () {
      btn.classList.toggle('visible', window.scrollY > 300);
    }, { passive: true });
  }

  // ---------- 初期化 ----------

  document.addEventListener('DOMContentLoaded', function () {
    // 中ペインは章選択前は全て非表示
    document.querySelectorAll('.para-nav-item').forEach(function (el) {
      el.style.display = 'none';
    });

    attachGlossaryListeners();
    setupScrollSync();
    setupBackToTop();

    var observer = new MutationObserver(function (mutations) {
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
      var uid = location.hash.slice('#para-'.length);
      var target = document.getElementById('para-' + uid);
      if (target) {
        setTimeout(function () {
          target.scrollIntoView({ behavior: 'smooth', block: 'start' });
          window.toggleParagraph(uid);
        }, 300);
      }
    }
  });

})();
