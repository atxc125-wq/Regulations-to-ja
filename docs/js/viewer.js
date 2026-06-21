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

  window.selectParagraph = function (uid, annexId) {
    var target = null;
    // UID重複対策: AnnexIDが指定されていれば data-annex-id+data-uid で絞り込む
    if (annexId) {
      target = document.querySelector(
        '.para-card[data-annex-id="' + annexId + '"][data-uid="' + uid + '"]'
      );
    }
    if (!target) {
      target = document.getElementById('para-' + uid);
    }
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
    updateMobileParaRow(uid);
  }

  function scrollMiddlePaneTo(uid) {
    var item = document.querySelector('.para-nav-item[data-uid="' + uid + '"]');
    var pane = document.getElementById('pane-paragraphs');
    if (!item || !pane) return;
    var paneRect = pane.getBoundingClientRect();
    var itemRect = item.getBoundingClientRect();
    if (itemRect.top < paneRect.top || itemRect.bottom > paneRect.bottom) {
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
      // 附属書段落・重複番号は非表示
      var isFirstOcc = el.dataset.dup !== '1';
      var isAnnex = el.dataset.annex === '1';
      var show = (isChild || isSelf) && isFirstOcc && !isAnnex;

      el.style.display = show ? '' : 'none';
      el.classList.remove('has-nav-children', 'nav-expanded');
      delete el.dataset.deepHidden;
      if (show) anyVisible = true;
    });

    // level > 3 を折りたたむ
    allItems.forEach(function (el) {
      if (el.style.display === 'none') return;
      var level = parseInt(el.dataset.level || '1', 10);
      if (level > 3) {
        el.style.display = 'none';
        el.dataset.deepHidden = '1';
      }
    });

    // has-nav-children を付与（直接の子が deepHidden な項目）
    allItems.forEach(function (el) {
      if (el.style.display === 'none' && !el.dataset.deepHidden) return;
      var num = el.dataset.number;
      if (!num) return;
      var hasHiddenChild = false;
      allItems.forEach(function (child) {
        if (child.dataset.deepHidden && child.dataset.parent === num) {
          hasHiddenChild = true;
        }
      });
      el.classList.toggle('has-nav-children', hasHiddenChild);
    });

    var placeholder = document.getElementById('para-nav-placeholder');
    if (placeholder) placeholder.style.display = anyVisible ? 'none' : '';

    // 中ペイン区切りの表示を同期
    document.querySelectorAll('.para-nav-divider').forEach(function (div) {
      div.style.display = (div.dataset.dividerFor === String(chapterNumber)) ? '' : 'none';
    });

    document.querySelectorAll('#chapter-tree .tree-btn').forEach(function (btn) {
      var item = btn.closest('.tree-item');
      btn.classList.toggle('active', item && item.dataset.number === chapterNumber);
    });

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

  // ---------- 左ペイン: 附属書クリック → 中ペイン絞り込み ----------

  window.selectAnnex = function (annexId) {
    var anyVisible = false;
    var allItems = document.querySelectorAll('.para-nav-item');
    allItems.forEach(function (el) {
      el.classList.remove('has-nav-children', 'nav-expanded');
      delete el.dataset.deepHidden;
      var show = el.dataset.annexId === String(annexId);
      el.style.display = show ? '' : 'none';
      if (show) anyVisible = true;
    });

    // level > 3 を折りたたむ
    allItems.forEach(function (el) {
      if (el.style.display === 'none') return;
      var level = parseInt(el.dataset.level || '1', 10);
      if (level > 3) {
        el.style.display = 'none';
        el.dataset.deepHidden = '1';
      }
    });

    // has-nav-children を付与（直接の子が deepHidden な項目）
    allItems.forEach(function (el) {
      if (el.style.display === 'none' && !el.dataset.deepHidden) return;
      var num = el.dataset.number;
      if (!num) return;
      var hasHiddenChild = false;
      allItems.forEach(function (child) {
        if (child.dataset.deepHidden && child.dataset.parent === num) {
          hasHiddenChild = true;
        }
      });
      el.classList.toggle('has-nav-children', hasHiddenChild);
    });

    var placeholder = document.getElementById('para-nav-placeholder');
    if (placeholder) placeholder.style.display = anyVisible ? 'none' : '';

    // 中ペイン区切りの表示を同期
    document.querySelectorAll('.para-nav-divider').forEach(function (div) {
      div.style.display = (div.dataset.dividerAnnex === String(annexId)) ? '' : 'none';
    });

    // 章ツリーのアクティブを解除し、附属書ツリーのアクティブを更新
    document.querySelectorAll('#chapter-tree .tree-btn').forEach(function (btn) {
      btn.classList.remove('active');
    });
    document.querySelectorAll('#annex-tree .tree-btn').forEach(function (btn) {
      var item = btn.closest('.tree-item');
      btn.classList.toggle('active', item && item.dataset.number === 'annex-' + annexId);
    });

    closeMobilePane();

    // モバイルナビバーを更新
    var annexItem = document.querySelector('#annex-tree .tree-item[data-number="annex-' + annexId + '"]');
    if (annexItem) {
      var jaEl = annexItem.querySelector('.tree-title-ja');
      var enEl = annexItem.querySelector('.tree-title-en');
      var title = (jaEl && jaEl.textContent.trim()) || (enEl && enEl.textContent.trim()) || '';
      var mobileEl = document.getElementById('mobile-chapter-text');
      if (mobileEl) mobileEl.textContent = 'Annex ' + annexId + (title ? ' ' + title : '');
    }

    // 最初の可視項目へスクロール
    // UID重複があるため getElementById ではなく data-annex-id で右ペインを検索
    // 画像のみの附属書（本文段落なし）にも対応するため para-figure も対象にする
    var firstCard = document.querySelector('.para-card[data-annex-id="' + annexId + '"], .para-figure[data-annex-id="' + annexId + '"]');
    if (firstCard) firstCard.scrollIntoView({ behavior: 'smooth', block: 'start' });
  };

  // ---------- 参照元に戻るバー ----------

  var _refBackSource = null; // { uid, number, title, annexId }

  window.selectRefParagraph = function (sourceUid, targetUid, targetAnnexId) {
    // 呼び出し元カードから段落番号・タイトルを収集
    var sourceCard = document.getElementById('para-' + sourceUid);
    var number = sourceCard ? (sourceCard.dataset.number || '') : '';
    var annexId = sourceCard ? (sourceCard.dataset.annexId || null) : null;

    // タイトルは中ペインのナビ項目 → 右ペインのカードヘッダーの順で取得
    var title = '';
    var navItem = document.querySelector('.para-nav-item[data-uid="' + sourceUid + '"]');
    if (navItem) {
      var jaEl = navItem.querySelector('.tree-title-ja');
      var enEl = navItem.querySelector('.tree-title-en');
      title = (jaEl && jaEl.textContent.trim()) || (enEl && enEl.textContent.trim()) || '';
    }
    if (!title && sourceCard) {
      var hJa = sourceCard.querySelector('.para-title-block .tree-title-ja');
      var hEn = sourceCard.querySelector('.para-title-block .tree-title-en');
      title = (hJa && hJa.textContent.trim()) || (hEn && hEn.textContent.trim()) || '';
    }

    _refBackSource = { uid: sourceUid, number: number, title: title, annexId: annexId };
    showBackBar();
    window.selectParagraph(targetUid, targetAnnexId);

    // 参照先カードを selectParagraph と同じロジックで特定してから展開
    var targetCard = null;
    if (targetAnnexId) {
      targetCard = document.querySelector(
        '.para-card[data-annex-id="' + targetAnnexId + '"][data-uid="' + targetUid + '"]'
      );
    }
    if (!targetCard) { targetCard = document.getElementById('para-' + targetUid); }
    if (targetCard) {
      var targetBody = targetCard.querySelector('.para-card-body');
      if (targetBody && targetBody.hidden) {
        targetBody.hidden = false;
        targetCard.classList.add('expanded');
      }
    }
  };

  function showBackBar() {
    if (!_refBackSource) return;
    var bar = document.getElementById('back-bar');
    var textEl = document.getElementById('back-bar-text');
    if (!bar) return;
    var label = '§ ' + _refBackSource.number;
    if (_refBackSource.title) label += '　' + _refBackSource.title;
    if (textEl) textEl.textContent = label;
    bar.classList.add('visible');
    bar.setAttribute('aria-hidden', 'false');
    // back-to-top ボタンをバーの上に逃がす
    var btt = document.getElementById('back-to-top');
    if (btt) btt.style.bottom = '4rem';
  }

  function hideBackBar() {
    _refBackSource = null;
    var bar = document.getElementById('back-bar');
    if (bar) {
      bar.classList.remove('visible');
      bar.setAttribute('aria-hidden', 'true');
    }
    var btt = document.getElementById('back-to-top');
    if (btt) btt.style.bottom = '';
  }

  window.goBackFromRef = function () {
    if (!_refBackSource) return;
    var src = _refBackSource;
    hideBackBar();
    window.selectParagraph(src.uid, src.annexId);
    // 元の段落が閉じていれば開く
    var body = document.getElementById('body-' + src.uid);
    if (body && body.hidden) {
      window.toggleParagraph(src.uid);
    }
  };

  window.dismissBackBar = function (event) {
    event.stopPropagation();
    hideBackBar();
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

  // ---------- 中ペイン: 折りたたみツリー ----------

  window.toggleNavExpand = function (uid) {
    var item = document.querySelector('.para-nav-item[data-uid="' + uid + '"]');
    if (!item || !item.classList.contains('has-nav-children')) return;

    var isExpanded = item.classList.contains('nav-expanded');
    item.classList.toggle('nav-expanded', !isExpanded);

    var num = item.dataset.number;
    if (isExpanded) {
      collapseNavChildren(num);
    } else {
      expandNavChildren(num);
    }
  };

  function expandNavChildren(parentNum) {
    document.querySelectorAll('.para-nav-item[data-parent="' + parentNum + '"]').forEach(function (child) {
      if (!child.dataset.deepHidden) return;
      child.style.display = '';
      delete child.dataset.deepHidden;
      // この子にもさらに子があれば has-nav-children を付与
      var childNum = child.dataset.number;
      var hasHiddenGrandchild = document.querySelector('.para-nav-item[data-parent="' + childNum + '"][data-deep-hidden]') !== null
        || (function () {
          var all = document.querySelectorAll('.para-nav-item[data-parent="' + childNum + '"]');
          for (var i = 0; i < all.length; i++) { if (all[i].dataset.deepHidden) return true; }
          return false;
        })();
      child.classList.toggle('has-nav-children', hasHiddenGrandchild);
    });
  }

  function collapseNavChildren(parentNum) {
    document.querySelectorAll('.para-nav-item[data-parent="' + parentNum + '"]').forEach(function (child) {
      child.style.display = 'none';
      child.dataset.deepHidden = '1';
      child.classList.remove('nav-expanded', 'has-nav-children');
      collapseNavChildren(child.dataset.number);
    });
  }

  // ---------- モバイルナビバー ----------

  function updateMobileChapterRow(num, title) {
    var el = document.getElementById('mobile-chapter-text');
    if (!el) return;
    el.textContent = num + (title ? ' ' + title : '');
  }

  function updateMobileParaRow(uid) {
    var el = document.getElementById('mobile-para-text');
    if (!el || !uid) return;
    var item = document.querySelector('.para-nav-item[data-uid="' + uid + '"]');
    if (!item) return;
    var numEl  = item.querySelector('.tree-num');
    var jaEl   = item.querySelector('.tree-title-ja');
    var enEl   = item.querySelector('.tree-title-en');
    var num    = numEl  ? numEl.textContent.trim() : '';
    var title  = (jaEl && jaEl.textContent.trim()) || (enEl && enEl.textContent.trim()) || '';
    el.textContent = num + (title ? ' ' + title : '');
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
      // ドロワーを開く: モバイルナビバーの直下に表示（position:fixed なので viewport 基準）
      var mobileNav = document.getElementById('mobile-nav');
      var navBottom = mobileNav ? mobileNav.getBoundingClientRect().bottom : 0;
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
  var _activeGlossaryEl = null;

  function escapeHtml(str) {
    return (str || '').replace(/[&<>"']/g, function (c) {
      return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c];
    });
  }

  function buildTooltipHtml(el) {
    var term   = el.dataset.term || '';
    var jaTerm = el.dataset.jaTerm || '';
    // 日本語アノテーションの場合: 「日本語用語（英語名）」を見出しに表示
    var header = jaTerm ? jaTerm + '（' + term + '）' : term;
    return '<div class="tooltip-term">' + escapeHtml(header) + '</div>' +
      '<div class="tooltip-def">' + escapeHtml(el.dataset.definition || '') + '</div>' +
      (el.dataset.ref ? '<div class="tooltip-ref">定義: § ' + escapeHtml(el.dataset.ref) + '</div>' : '');
  }

  function showTooltip(event) {
    if (!tooltip) return;
    tooltip.innerHTML = buildTooltipHtml(event.currentTarget);
    tooltip.classList.add('visible');
    positionTooltip(event);
  }

  function moveTooltip(event) { positionTooltip(event); }

  function hideTooltip() {
    if (tooltip) tooltip.classList.remove('visible');
    _activeGlossaryEl = null;
  }

  function positionTooltip(event) {
    if (!tooltip) return;
    var margin = 8;
    var vw = window.innerWidth;
    var vh = window.innerHeight;
    var tw = tooltip.offsetWidth;
    var th = tooltip.offsetHeight;
    var x, y;

    // タッチ / クリック: 要素の直下（または上）に配置
    if (event.type === 'click' || (event.touches && event.touches.length > 0)) {
      var rect = event.currentTarget.getBoundingClientRect();
      x = rect.left;
      y = rect.bottom + margin;
      if (y + th > vh - margin) { y = rect.top - th - margin; }
    } else {
      // マウスホバー: カーソルの右下（または左上）に配置
      x = event.clientX + margin;
      y = event.clientY + margin;
      if (y + th > vh - margin) { y = event.clientY - th - margin; }
    }

    // 画面内に収まるよう最終クランプ
    x = Math.max(margin, Math.min(x, vw - tw - margin));
    y = Math.max(margin, Math.min(y, vh - th - margin));
    tooltip.style.left = x + 'px';
    tooltip.style.top  = y + 'px';
  }

  // モバイル: タップでツールチップをトグル
  function handleGlossaryTap(event) {
    if (_activeGlossaryEl === event.currentTarget) {
      hideTooltip();
    } else {
      _activeGlossaryEl = event.currentTarget;
      tooltip.innerHTML = buildTooltipHtml(event.currentTarget);
      tooltip.classList.add('visible');
      positionTooltip(event);
    }
  }

  function attachOneGlossaryListener(el) {
    el.addEventListener('mouseenter', showTooltip);
    el.addEventListener('mousemove',  moveTooltip);
    el.addEventListener('mouseleave', hideTooltip);
    el.addEventListener('focus',      showTooltip);
    el.addEventListener('blur',       hideTooltip);
    el.addEventListener('click',      handleGlossaryTap);
  }

  function attachGlossaryListeners() {
    document.querySelectorAll('.glossary-term').forEach(attachOneGlossaryListener);
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

    // タップ: 用語以外の場所をタップでツールチップを閉じる
    document.addEventListener('click', function (event) {
      if (!_activeGlossaryEl || !tooltip) return;
      if (!_activeGlossaryEl.contains(event.target) &&
          !tooltip.contains(event.target)) {
        hideTooltip();
      }
    });

    var observer = new MutationObserver(function (mutations) {
      mutations.forEach(function (m) {
        m.addedNodes.forEach(function (node) {
          if (node.nodeType !== 1) return;
          node.querySelectorAll && node.querySelectorAll('.glossary-term').forEach(attachOneGlossaryListener);
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
    } else {
      // デフォルト: 第1章を自動選択して中ペインを表示する
      var firstChapter = document.querySelector('#chapter-tree .tree-item[data-number]');
      if (firstChapter) {
        window.selectChapter(firstChapter.dataset.number);
      }
    }
  });

})();
