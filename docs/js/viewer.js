/**
 * viewer.js  --  3ペインビューワの操作ロジック
 *
 * 担当:
 *  - アコーディオン開閉
 *  - 左ペイン章クリック → 中ペインに該当段落を絞り込み
 *  - 中ペイン段落クリック → 右ペインにスクロール
 *  - グロッサリー ツールチップ
 */

(function () {
  'use strict';

  // ---------- アコーディオン ----------

  /**
   * 段落カードの展開/折りたたみを切り替える。
   * @param {string} uid
   */
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

  /**
   * 右ペインの特定段落にスクロールし、中ペインのアクティブを更新する。
   * @param {string} uid
   */
  window.selectParagraph = function (uid) {
    const target = document.getElementById('para-' + uid);
    if (target) {
      target.scrollIntoView({ behavior: 'smooth', block: 'start' });
    }

    // 中ペインのactiveを更新
    document.querySelectorAll('.para-nav-item').forEach(function (el) {
      el.classList.toggle('active', el.dataset.uid === uid);
    });
  };

  // ---------- 左ペイン: 章クリック → 中ペイン絞り込み ----------

  /**
   * 中ペインの段落ナビを、指定した章番号の子段落のみ表示する。
   * @param {string} chapterNumber
   */
  window.selectChapter = function (chapterNumber) {
    const allItems = document.querySelectorAll('.para-nav-item');
    allItems.forEach(function (el) {
      const parent = el.dataset.parent || '';
      const uid = el.dataset.uid;
      const card = document.getElementById('para-' + uid);

      // chapterNumber の直接子 or 孫を表示（parent が chapterNumber で始まる）
      const isChild = parent === chapterNumber || parent.startsWith(chapterNumber + '.');
      // chapterNumber そのものの段落も表示
      const isSelf = card && card.dataset.number === chapterNumber;

      el.style.display = (isChild || isSelf) ? '' : 'none';
    });

    // 左ペインのactiveを更新
    document.querySelectorAll('#chapter-tree .tree-btn').forEach(function (btn) {
      const item = btn.closest('.tree-item');
      btn.classList.toggle('active', item && item.dataset.number === chapterNumber);
    });

    // 右ペインを章の最初の段落にスクロール
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

    // 本文が閉じていれば開く
    if (body && body.hidden) {
      window.toggleParagraph(uid);
    }
    // スクロール
    box.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    box.style.outline = '2px solid #e9a80b';
    setTimeout(function () { box.style.outline = ''; }, 1500);
  };

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

    // 最初のMutationObserverで動的に追加された用語スパンにも対応
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

    // URLハッシュで特定段落に直リンク対応 (#para-UIDXXX)
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
