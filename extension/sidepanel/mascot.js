/* NyayaBot mascot artwork shared with the main app's warm gold visual theme. */
(function (root) {
  'use strict';

  function mascotSvg(mood) {
    const safeMood = String(mood || 'wait').replace(/[^a-z-]/gi, '');
    return `<img class="mascot-image" src="nyayabot-voice-orb.png" alt="NyayaBot helper, ${safeMood}">`;
  }

  root.mascotSvg = mascotSvg;
})(window);
