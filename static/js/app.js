
// =============================================
// UTILS
// =============================================
var BASE = window.location.origin;
function $(id){return document.getElementById(id);}
function fmt(s){return s===undefined?'—':s;}


// ===== ANNOUNCEMENT BANNER =====
(function(){
  var HIDE_KEY = 'ann_hide_until';
  function shouldShow(){
    var t = localStorage.getItem(HIDE_KEY);
    if(!t) return true;
    var n = parseInt(t, 10);
    if(isNaN(n)){ localStorage.removeItem(HIDE_KEY); return true; }
    return Date.now() > n;
  }
  function loadAnnouncement(){
    fetch('/api/announcement').then(function(r){return r.json();}).then(function(d){
      var txt = d.text || '';
      if(!txt.trim()) return; // no announcement, don't show
      var el = document.getElementById('annContent');
      if(el) el.textContent = txt;
      // Load into admin textarea too
      var ta = document.getElementById('annAdminText');
      if(ta) ta.value = txt;
      if(shouldShow()){
        var ov = document.getElementById('announceOverlay');
        if(ov){ ov.classList.add('show'); }
      }
    }).catch(function(){});
  }
  window.closeAnnounce = function(){
    var ov = document.getElementById('announceOverlay');
    if(ov){ ov.classList.remove('show'); }
  };
  window.hideAnnounce2h = function(){
    localStorage.setItem('ann_hide_until', String(Date.now() + 2*60*60*1000));
    window.closeAnnounce();
  };
  window.saveAnnouncement = function(){
    var ta = document.getElementById('annAdminText');
    var st = document.getElementById('annSaveStatus');
    if(!ta) return;
    var txt = ta.value;
    if(st) st.textContent = 'Đang lưu...';
    adminFetch('/api/admin/update_announcement',
      {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({text:txt})},
      function(d){
        if(st) st.textContent = '✓ Đã lưu ' + new Date().toLocaleTimeString('vi');
        // update live content
        var el = document.getElementById('annContent');
        if(el) el.textContent = txt;
        toast(true, 'Đã lưu thông báo!');
      },
      function(err){ if(st) st.textContent = '✗ Lỗi: ' + (err||'Không thể lưu'); toast(false,'Lưu thất bại'); }
    );
  };
  // Load after startup screen finishes (~1.5s)
  setTimeout(loadAnnouncement, 1600);
})();

function toast(ok, msg){
  var ov=$('toast-overlay'),box=$('toast-box'),sq=$('toast-square'),ii=$('toast-i'),lbl=$('toast-label');
  box.className='toast-box '+(ok?'toast-box-success':'toast-box-error');
  sq.className='toast-square'+(ok?'':' toast-sq-error');
  lbl.className='toast-label'+(ok?'':' toast-lbl-error');
  ii.className='fa-solid '+(ok?'fa-check':'fa-xmark');
  lbl.textContent=msg?msg:(ok?'Thành công':'Thất bại');
  ov.classList.remove('toast-fade-out');
  box.classList.remove('toast-fade-out');
  ov.classList.add('show');
  setTimeout(function(){
    box.classList.add('toast-fade-out');
    setTimeout(function(){ov.classList.remove('show');box.classList.remove('toast-fade-out');},420);
  },2500);
}

function copyText(t){
  if(!t||t==='Đang tải...')return;
  navigator.clipboard.writeText(t).then(function(){toast(true,'Đã sao chép!');}).catch(function(){
    var a=document.createElement('textarea');a.value=t;document.body.appendChild(a);a.select();document.execCommand('copy');document.body.removeChild(a);toast(true,'Đã sao chép!');
  });
}

// Admin-aware fetch — tự động xử lý lỗi kết nối và session hết hạn
function adminFetch(url, options, onSuccess, onError){
  var opts = options || {};
  opts.credentials = 'same-origin';
  opts.headers = opts.headers || {};
  fetch(url, opts)
  .then(function(r){
    if(r.status===401){
      hideLoad();
      alert('⚠️ Phiên đăng nhập đã hết hạn. Vui lòng đăng nhập lại!');
      location.reload();
      return null;
    }
    if(r.status===404){
      hideLoad();
      if(onError) onError({status:'error',message:'Không tìm thấy API endpoint: '+url});
      return null;
    }
    return r.json();
  })
  .then(function(d){
    if(d && onSuccess) onSuccess(d);
  })
  .catch(function(err){
    hideLoad();
    console.error('adminFetch error:', url, err);
    if(onError) onError({status:'error',message:'Lỗi kết nối: '+err.message});
    else toast(false,'Mất kết nối');
  });
}

function showLoad(){$('loadOverlay').style.display='flex';}
function hideLoad(){$('loadOverlay').style.display='none';}

function showLogin(){$('loginOverlay').classList.add('show');}
function closeLogin(){$('loginOverlay').classList.remove('show');}

function closeNav(){
  $('navDrop').classList.remove('show');
  var bk=$('navBackdrop'); if(bk)bk.classList.remove('show');
  $('hbg').classList.remove('open');
  document.body.style.overflow='';
}

function toggleMenu(){
  $('hbg').classList.toggle('open');
  var open=$('navDrop').classList.toggle('show');
  var bk=$('navBackdrop');
  if(bk)bk.classList.toggle('show', !!open);
  document.body.style.overflow=open?'hidden':'';
}

// Close nav when clicking outside
document.addEventListener('click', function(e){
  var nd=$('navDrop'),hb=$('hbg');
  if(nd && nd.classList.contains('show') && !nd.contains(e.target) && !hb.contains(e.target)){
    closeNav();
  }
});

// Tab switching
var curTab='trangchu';
function sw(name, closeMenu){
  if(curTab===name && !closeMenu)return;
  var old=$('tab-'+curTab);
  var nw=$('tab-'+name);
  if(!nw)return;
  if(old){old.classList.remove('active');}
  nw.classList.add('active');
  curTab=name;
  // Update nav active state
  document.querySelectorAll('.nav-item').forEach(function(el){
    el.classList.remove('active');
  });
  closeNav();
  // Load data for tab
  if(name==='database')loadDatabase();
  if(name==='weblog')loadWebLog();
  if(name==='keystats')loadStats();
  if(name==='keyfree')loadFreeKeys();
  if(name==='devicereview')loadDeviceRequests();
  if(name==='getkeyconfig')loadGetkeyConfig();
  if(name==='settings')loadApiUrls();
}

// =============================================
// LOGIN
// =============================================
$('loginForm').addEventListener('submit',function(e){
  e.preventDefault();
  var u=$('lu').value.trim(),p=$('lp').value.trim();
  if(!u||!p)return;
  $('loginErr').style.display='none';
  $('loginSpinner').style.display='block';
  $('loginForm').style.display='none';
  fetch('/login',{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/x-www-form-urlencoded'},body:'user='+encodeURIComponent(u)+'&pass='+encodeURIComponent(p)})
  .then(function(r){return r.json();})
  .then(function(d){
    $('loginSpinner').style.display='none';
    $('loginForm').style.display='block';
    if(d.status==='success'||d.success){toast(true);setTimeout(function(){location.reload();},600);}
    else{$('loginErr').textContent=d.message||'Sai tài khoản hoặc mật khẩu';$('loginErr').style.display='block';}
  }).catch(function(){
    $('loginSpinner').style.display='none';
    $('loginForm').style.display='block';
    $('loginErr').textContent='Lỗi kết nối';$('loginErr').style.display='block';
  });
});

// =============================================
// MUSIC PLAYER
// =============================================
var tracks=['/nhac.mp3','/nhac2.mp3','/nhac3.mp3'];
var trackNames=['Nhạc 1','Nhạc 2','Nhạc 3'];
var curTrack=0;
var scTrack={url:null,title:null,cover:null};
var isScTrack=false;
var audio=$('bgAudio');
var isPlaying=false;

function fmtTime(s){
  if(isNaN(s)||!isFinite(s))return'0:00';
  var m=Math.floor(s/60),sc=Math.floor(s%60);
  return m+':'+(sc<10?'0':'')+sc;
}

function setEq(on){
  var delays=['0s','0.15s','0.3s','0.1s','0.25s'];
  for(var i=1;i<=5;i++){
    var b=$('eq'+i);
    if(on){b.classList.add('active');b.style.animationDelay=delays[i-1];b.style.animationDuration=(0.45+Math.random()*0.3)+'s';}
    else{b.classList.remove('active');}
  }
}

function updateTrackUI(){
  for(var i=0;i<3;i++){
    var b=$('trk'+i);
    if(b){b.classList.toggle('playing',!isScTrack && i===curTrack);}
  }
  var vn=$('vinylDisc'),cs=$('djCoverSm');
  if(isScTrack && scTrack.cover){
    cs.src=scTrack.cover; cs.style.display='block'; vn.style.display='none';
    $('musicTitle').textContent=scTrack.title||'SoundCloud Track';
  } else {
    cs.style.display='none'; vn.style.display='flex';
    $('musicTitle').textContent=trackNames[curTrack];
  }
}

function switchTrack(idx){
  isScTrack=false; curTrack=idx; isPlaying=false;
  audio.src=tracks[idx]; audio.load();
  $('playIcon').className='fa-solid fa-play';
  $('musicStatus').innerHTML='<i class="fa-solid fa-pause"></i> Tạm dừng';
  $('vinylDisc').classList.remove('spin');
  setEq(false);
  updateTrackUI();
}

function togglePlay(){
  if(audio.paused){
    audio.play().then(function(){
      isPlaying=true;
      $('playIcon').className='fa-solid fa-pause';
      $('musicStatus').innerHTML='<i class="fa-solid fa-play"></i> Đang phát';
      $('vinylDisc').classList.add('spin');
      setEq(true);
    }).catch(function(){});
  } else {
    audio.pause();
    isPlaying=false;
    $('playIcon').className='fa-solid fa-play';
    $('musicStatus').innerHTML='<i class="fa-solid fa-pause"></i> Tạm dừng';
    $('vinylDisc').classList.remove('spin');
    setEq(false);
  }
}

function prevTrack(){
  if(isScTrack){isScTrack=false;switchTrack(curTrack);return;}
  switchTrack((curTrack-1+3)%3);
}
function nextTrack(){
  if(isScTrack){isScTrack=false;switchTrack((curTrack+1)%3);return;}
  switchTrack((curTrack+1)%3);
}

var isMuted=false;
function toggleMute(){
  isMuted=!isMuted; audio.muted=isMuted;
  $('volIcon').className='fa-solid '+(isMuted?'fa-volume-xmark':'fa-volume-high');
}

function onSeek(){
  if(audio.duration)audio.currentTime=($('seekBar').value/100)*audio.duration;
}

audio.addEventListener('timeupdate',function(){
  if(!audio.duration)return;
  var p=(audio.currentTime/audio.duration)*100;
  $('seekBar').value=p;
  $('curTime').textContent=fmtTime(audio.currentTime);
  $('totTime').textContent=fmtTime(audio.duration);
});
audio.addEventListener('ended',function(){nextTrack();});
audio.addEventListener('loadedmetadata',function(){$('totTime').textContent=fmtTime(audio.duration);});
var _scRetrying=false;
audio.addEventListener('error',function(){
  isPlaying=false;
  $('playIcon').className='fa-solid fa-play';
  $('vinylDisc').classList.remove('spin');
  setEq(false);
  if(isScTrack && scTrack.scPageUrl && !_scRetrying){
    // Auto-retry: re-fetch stream URL using original SoundCloud page URL (CDN links expire)
    _scRetrying=true;
    $('musicStatus').innerHTML='<i class="fa-solid fa-rotate fa-spin"></i> Đang kết nối lại...';
    var fd=new FormData(); fd.append('url',scTrack.scPageUrl);
    fetch('/api/get_stream_url',{method:'POST',body:fd})
    .then(function(r){return r.json();})
    .then(function(d){
      _scRetrying=false;
      if(d.status==='success'&&d.stream_url){
        audio.src=d.stream_url; audio.load();
        audio.play().then(function(){
          isPlaying=true;
          $('playIcon').className='fa-solid fa-pause';
          $('musicStatus').innerHTML='<i class="fa-solid fa-play"></i> Đang phát';
          $('vinylDisc').classList.add('spin');
          setEq(true);
        }).catch(function(){
          isScTrack=false;
          $('musicStatus').innerHTML='<i class="fa-solid fa-triangle-exclamation"></i> Không thể phát';
        });
      } else {
        isScTrack=false;
        $('musicTitle').textContent='SoundCloud - Lỗi';
        $('musicStatus').innerHTML='<i class="fa-solid fa-triangle-exclamation"></i> Lỗi phát nhạc';
        $('djCoverSm').style.display='none';
        $('vinylDisc').style.display='flex';
      }
    }).catch(function(){
      _scRetrying=false;
      isScTrack=false;
      $('musicStatus').innerHTML='<i class="fa-solid fa-triangle-exclamation"></i> Mất kết nối';
    });
  } else {
    $('musicStatus').innerHTML='<i class="fa-solid fa-triangle-exclamation"></i> Lỗi phát nhạc';
    if(isScTrack){
      _scRetrying=false; isScTrack=false;
      $('djCoverSm').style.display='none'; $('vinylDisc').style.display='flex';
    }
  }
});

// Init first track
audio.src=tracks[0];

// =============================================
// SOUNDCLOUD SEARCH
// =============================================
var selectedSong=null;

function scSearch(){
  var q=$('scQuery').value.trim();
  if(!q)return;
  $('scBtn').disabled=true;
  $('scResults').style.display='none';
  $('scEmpty').style.display='none';
  $('scError').style.display='none';
  $('scLoading').style.display='block';
  selectedSong=null; _scSongs=[]; $('scListenBtn').disabled=true;
  fetch('/api/search_music?q='+encodeURIComponent(q))
  .then(function(r){return r.json();})
  .then(function(d){
    $('scLoading').style.display='none';
    $('scBtn').disabled=false;
    if(d.status==='success' && d.songs && d.songs.length>0){
      $('scResults').style.display='block';
      renderSongList(d.songs);
    } else if(d.status==='success'){
      $('scEmpty').style.display='block';
    } else {
      var scErrMsg=$('scErrorMsg'); if(scErrMsg)scErrMsg.textContent=d.message||'Lỗi tìm kiếm';
      $('scError').style.display='block';
    }
  }).catch(function(e){
    $('scLoading').style.display='none';
    $('scBtn').disabled=false;
    var scErrMsg=$('scErrorMsg'); if(scErrMsg)scErrMsg.textContent='Lỗi kết nối: '+e.message+'. Vui lòng thử lại!';
    $('scError').style.display='block';
  });
}

// Global song list — avoids double-JSON / HTML-entity issues in onclick attrs
var _scSongs = [];

function renderSongList(songs){
  _scSongs = songs; // store in global array, reference by index only
  var html='';
  songs.forEach(function(s,i){
    var cover=s.cover?('<img class="sc-cover" src="'+s.cover+'" alt="" onerror="this.hidden=true">'):('<div class="sc-cover-placeholder"><i class="fa-solid fa-music"></i></div>');
    html+='<div class="sc-song-card" id="sc_card_'+i+'" onclick="selectSong('+i+')">'
    +cover
    +'<div class="sc-song-info"><div class="sc-song-title">'+s.title.replace(/</g,'&lt;').replace(/>/g,'&gt;')+'</div><div class="sc-song-meta"><i class="fa-brands fa-soundcloud"></i> SoundCloud</div></div>'
    +'<div class="sc-sel-indicator" id="sc_sel_'+i+'"><i class="fa-solid fa-check" style="font-size:0.65rem;"></i></div>'
    +'</div>';
  });
  $('scSongList').innerHTML=html;
}

function selectSong(idx){
  if(idx < 0 || idx >= _scSongs.length) return;
  selectedSong = _scSongs[idx];
  document.querySelectorAll('.sc-song-card').forEach(function(el,i){
    el.classList.toggle('selected',i===idx);
  });
  $('scListenBtn').disabled=false;
}

function scListen(){
  if(!selectedSong)return;
  $('scListenBtn').disabled=true;
  $('scListenBtn').innerHTML='<div class="spinner spinner-sm" style="border-top-color:#fff;border-color:rgba(255,255,255,0.3);"></div> Đang lấy link...';
  var fd=new FormData(); fd.append('url',selectedSong.url);
  fetch('/api/get_stream_url',{method:'POST',body:fd})
  .then(function(r){return r.json();})
  .then(function(d){
    $('scListenBtn').innerHTML='<i class="fa-solid fa-play"></i> Nghe Bài Này';
    $('scListenBtn').disabled=false;
    if(d.status==='success'){
      isScTrack=true;
      scTrack.url=d.stream_url;
      scTrack.scPageUrl=selectedSong.url; // SoundCloud page URL for retry
      scTrack.title=d.title||selectedSong.title;
      scTrack.cover=d.cover||selectedSong.cover||'';
      audio.src=d.stream_url; audio.load();
      updateTrackUI();
      audio.play().then(function(){
        isPlaying=true;
        $('playIcon').className='fa-solid fa-pause';
        $('musicStatus').innerHTML='<i class="fa-solid fa-play"></i> Đang phát';
        $('vinylDisc').classList.add('spin');
        setEq(true);
        sw('trangchu');
        setTimeout(function(){$('bgAudio').scrollIntoView({behavior:'smooth',block:'center'});},400);
      }).catch(function(){});
    } else {
      toast(false); alert('Lỗi: '+(d.message||'Không lấy được stream URL'));
    }
  }).catch(function(e){
    $('scListenBtn').innerHTML='<i class="fa-solid fa-play"></i> Nghe Bài Này';
    $('scListenBtn').disabled=false;
    toast(false);
  });
}

// =============================================
// GET KEY FREE
// =============================================
function doGetKey(){
  $('gkBtn').style.display='none';
  $('gkSpinner').style.display='block';
  $('gkLink').style.display='none';
  $('gkErr').style.display='none';
  fetch('/api/getkey',{method:'POST'})
  .then(function(r){return r.json();})
  .then(function(d){
    $('gkSpinner').style.display='none';
    if(d.status==='success'&&(d.shortenedUrl||d.link)){
      var url=d.shortenedUrl||d.link;
      window.location.href=url;
    } else {
      $('gkErr').textContent=d.message||'Lỗi tạo link';
      $('gkErr').style.display='block';
      $('gkBtn').style.display='block';
    }
  }).catch(function(){
    $('gkSpinner').style.display='none';
    $('gkErr').textContent='Lỗi kết nối';
    $('gkErr').style.display='block';
    $('gkBtn').style.display='block';
  });
}

// =============================================
// CHECK KEY (public)
// =============================================
function doCheckKey(){
  var k=$('ck_key').value.trim();
  if(!k){return;}
  showLoad();
  var fd=new FormData(); fd.append('key',k);
  fetch('/',{method:'POST',body:fd})
  .then(function(r){return r.json();})
  .then(function(d){
    hideLoad();
    var box=$('ck_result');
    box.style.display='block';
    if(d.exists){
      var st=d.key_status||'—';
      var stClass=(st==='Đã kích hoạt')?'badge-yes':(st==='Hết hạn'?'badge-no':'badge-warn');
      var stIcon=(st==='Đã kích hoạt')?'check':(st==='Hết hạn'?'xmark':'clock');
      var badge='<span class="badge '+stClass+'"><i class="fa-solid fa-'+stIcon+'"></i> '+st+'</span>';
      var usedc=d.used_devices!==undefined?d.used_devices:'—';
      var maxd=d.max_devices!==undefined?d.max_devices:'—';
      box.innerHTML='<div class="result-box"><div class="result-title"><i class="fa-solid fa-circle-info" style="color:var(--primary);"></i> Thông tin Key</div>'
        +'<div class="info-row"><span class="info-label">Key</span><span class="info-val key-val" style="font-size:0.68rem;word-break:break-all;">'+k+'</span></div>'
        +'<div class="info-row"><span class="info-label">Trạng thái</span><span class="info-val">'+badge+'</span></div>'
        +'<div class="info-row"><span class="info-label">Thời hạn cài</span><span class="info-val">'+fmt(d.duration)+'</span></div>'
        +'<div class="info-row"><span class="info-label">Ngày hết hạn</span><span class="info-val">'+fmt(d.expiry_date)+'</span></div>'
        +'<div class="info-row"><span class="info-label">Thiết bị</span><span class="info-val">'+fmt(usedc)+' / '+fmt(maxd)+'</span></div>'
        +'<div class="info-row"><span class="info-label">Kích hoạt lúc</span><span class="info-val">'+fmt(d.activated_time_str||d.activated_time)+'</span></div>'
        +'<div class="info-row"><span class="info-label">Ngày tạo</span><span class="info-val">'+fmt(d.created_at_str||d.created_at)+'</span></div>'
        +'</div>';
    } else {
      box.innerHTML='<div style="background:rgba(239,68,68,0.07);border:1px solid rgba(239,68,68,0.2);border-radius:12px;padding:16px;color:var(--danger);font-size:0.84rem;font-weight:700;text-align:center;"><i class="fa-solid fa-circle-xmark" style="font-size:1.5rem;display:block;margin-bottom:8px;"></i>'+(d.msg||d.message||'Mã Key không tồn tại trên hệ thống!')+'</div>';
    }
  }).catch(function(){hideLoad();});
}

if (window.IS_ADMIN) {
// =============================================
// DATABASE TAB
// =============================================
var dbAll=[];
function loadDatabase(){
  showLoad();
  fetch('/api/list_keys',{credentials:'same-origin'})
  .then(function(r){if(r.status===401){hideLoad();alert('Phiên đăng nhập hết hạn!');location.reload();return null;}return r.json();})
  .then(function(d){
    hideLoad();
    dbAll=Array.isArray(d)?d:(Array.isArray(d.data)?d.data:[]);
    renderDbTable();
  }).catch(function(){hideLoad();});
}

var _dbKeyArr=[];
function renderDbTable(){
  var f=$('db_filter').value.trim().toLowerCase();
  var rows=dbAll.filter(function(k){
    return !f||(k.key||'').toLowerCase().includes(f)||(k.creator_info||'').toLowerCase().includes(f)||(k.status||'').toLowerCase().includes(f);
  });
  _dbKeyArr=rows.map(function(k){return k.key;});
  var html='';
  rows.forEach(function(k,i){
    var st=k.status||'—';
    var locked=k.is_locked||false;
    var badge;
    if(locked) badge='<span class="badge badge-no"><i class="fa-solid fa-lock"></i> Khóa</span>';
    else if(st==='Đã kích hoạt') badge='<span class="badge badge-yes"><i class="fa-solid fa-check"></i> Hoạt động</span>';
    else if(st==='Chưa kích hoạt') badge='<span class="badge badge-warn"><i class="fa-solid fa-clock"></i> Chưa KH</span>';
    else badge='<span class="badge badge-no"><i class="fa-solid fa-xmark"></i> Hết hạn</span>';
    var typStr=(k.key||'').startsWith('FREE')?'FREE':((k.key||'').startsWith('VIP')?'VIP':'KEY');
    var tbStr=k.thiet_bi||'—';
    html+='<tr style="'+(locked?'opacity:0.65;':'')+'">'
      +'<td><span class="key-val" onclick="copyKeyBtn(\''+k.key.replace(/'/g,"\'")+'\')" style="cursor:pointer;font-size:0.65rem;" title="Click để copy">'+k.key+'</span></td>'
      +'<td><span class="badge badge-warn">'+typStr+'</span></td>'
      +'<td style="font-size:0.73rem;color:var(--text2);">'+fmt(k.han_dung||'—')+'</td>'
      +'<td style="text-align:center;">'+tbStr+'</td>'
      +'<td>'+badge+'</td>'
      +'<td><div class="td-actions" style="gap:3px;flex-wrap:wrap;">'
      +'<button class="btn-sm btn-sm-blue" title="Sao chép key" onclick="copyKeyBtn(\''+k.key.replace(/'/g,"\'")+'\')" style="padding:4px 7px;"><i class="fa-solid fa-copy"></i></button>'
      +'<button class="btn-sm btn-sm-warn" title="Gia hạn key" onclick="extendKeyPrompt(_dbKeyArr['+i+'])" style="padding:4px 7px;"><i class="fa-solid fa-clock"></i></button>'
      +'<button class="btn-sm" title="Reset thiết bị" onclick="resetDeviceBtn(_dbKeyArr['+i+'])" style="padding:4px 7px;background:rgba(59,130,246,0.1);color:#2563eb;border:1px solid rgba(59,130,246,0.25);border-radius:8px;"><i class="fa-solid fa-rotate"></i></button>'
      +'<button class="btn-sm" title="'+(locked?'Mở khóa':'Khóa')+' key" onclick="toggleLockKey(_dbKeyArr['+i+'],'+locked+')" style="padding:4px 7px;background:'+(locked?'rgba(34,197,94,0.1)':'rgba(239,68,68,0.1)')+';color:'+(locked?'#16a34a':'#ef4444')+';border:1px solid '+(locked?'rgba(34,197,94,0.25)':'rgba(239,68,68,0.25)')+';border-radius:8px;"><i class="fa-solid '+(locked?'fa-lock-open':'fa-lock')+'"></i></button>'
      +'<button class="btn-sm" title="Sao chép tạo key mới" onclick="cloneKey(_dbKeyArr['+i+'])" style="padding:4px 7px;background:rgba(249,115,22,0.1);color:var(--primary);border:1px solid rgba(249,115,22,0.25);border-radius:8px;"><i class="fa-solid fa-clone"></i></button>'
      +'<button class="btn-sm btn-sm-red" title="Xóa key" onclick="deleteKey(_dbKeyArr['+i+'])" style="padding:4px 7px;"><i class="fa-solid fa-trash"></i></button>'
      +'</div></td>'
      +'</tr>';
  });
  $('dbBody').innerHTML=html||'<tr><td colspan="6" style="text-align:center;color:var(--muted);padding:22px;">Không có key nào</td></tr>';
  $('db_page_info').textContent='Tổng: '+rows.length+' key'+(f?' (lọc từ '+dbAll.length+')':'');
}

function copyKeyBtn(key){
  navigator.clipboard.writeText(key).catch(function(){
    var a=document.createElement('textarea');a.value=key;document.body.appendChild(a);a.select();document.execCommand('copy');document.body.removeChild(a);
  });
  toast(true,'Đã sao chép!');
}

function resetDeviceBtn(key){
  if(!confirm('Reset tất cả thiết bị của key '+key+'?\nKey sẽ trở về trạng thái chưa kích hoạt.'))return;
  showLoad();
  fetch('/reset/'+encodeURIComponent(key),{credentials:'same-origin'})
  .then(function(r){
    if(r.status===401){hideLoad();alert('Phiên đăng nhập hết hạn!');location.reload();return null;}
    return r.json();
  })
  .then(function(d){if(d){hideLoad();toast(d.status==='success');if(d.status==='success')loadDatabase();}})
  .catch(function(e){hideLoad();toast(false,'Lỗi kết nối');});
}

function toggleLockKey(key,isLocked){
  var action=isLocked?'mở khóa':'khóa';
  if(!confirm((isLocked?'Mở khóa':'Khóa')+' key '+key+'?\n'+(isLocked?'Key sẽ có thể sử dụng được.':'Key sẽ bị vô hiệu hóa ngay lập tức.')))return;
  showLoad();
  var fd=new FormData(); fd.append('key',key);
  fetch('/api/lock_key',{method:'POST',body:fd,credentials:'same-origin'})
  .then(function(r){
    if(r.status===401){hideLoad();alert('Phiên đăng nhập hết hạn!');location.reload();return null;}
    return r.json();
  })
  .then(function(d){if(d){hideLoad();toast(d.status==='success');if(d.status==='success')loadDatabase();}})
  .catch(function(e){hideLoad();toast(false,'Lỗi kết nối');});
}

function cloneKey(key){
  if(!confirm('Tạo bản sao key '+key+'?\nSẽ tạo một key mới với cùng cấu hình.'))return;
  showLoad();
  var fd=new FormData(); fd.append('key',key);
  fetch('/api/copy_key',{method:'POST',body:fd,credentials:'same-origin'})
  .then(function(r){
    if(r.status===401){hideLoad();alert('Phiên đăng nhập hết hạn!');location.reload();return null;}
    return r.json();
  })
  .then(function(d){
    if(!d) return;
    hideLoad();
    if(d.status==='success'){
      toast(true,'Sao chép thành công!');
      if(d.new_key){copyKeyBtn(d.new_key);}
      loadDatabase();
    } else {
      toast(false, d.message||'Thất bại');
    }
  }).catch(function(e){hideLoad();toast(false,'Lỗi kết nối');});
}

function deleteKey(key){
  if(!confirm('Xóa key '+key+'?'))return;
  showLoad();
  fetch('/delete/'+encodeURIComponent(key),{credentials:'same-origin'})
  .then(function(r){
    if(r.status===401){hideLoad();alert('Phiên đăng nhập hết hạn, vui lòng đăng nhập lại!');location.reload();return null;}
    return r.json();
  })
  .then(function(d){if(d){hideLoad();toast(d.status==='success');if(d.status==='success')loadDatabase();}})
  .catch(function(e){hideLoad();toast(false,'Lỗi kết nối: '+e.message);});
}

// resetDevices is aliased to resetDeviceBtn (same function, kept for compatibility)
function resetDevices(key){ resetDeviceBtn(key); }

function extendKeyPrompt(key){
  var h=prompt('Gia hạn thêm bao nhiêu giờ?','24');
  if(!h||isNaN(h))return;
  showLoad();
  var fd=new FormData(); fd.append('key',key); fd.append('hours',h);
  fetch('/api/extend_key',{method:'POST',body:fd,credentials:'same-origin'})
  .then(function(r){
    if(r.status===401){hideLoad();alert('Phiên đăng nhập hết hạn!');location.reload();return null;}
    return r.json();
  })
  .then(function(d){if(d){hideLoad();toast(d.status==='success');if(d.status==='success')loadDatabase();}})
  .catch(function(e){hideLoad();toast(false,'Lỗi kết nối');});
}

// =============================================
// CREATE KEY
// =============================================
function updateExpiry(){
  var dur=parseInt($('tk_duration').value);
  var d=new Date(Date.now()+dur*1000);
  $('tk_expiry_preview').value=d.toLocaleString('vi-VN');
}
updateExpiry();

function doCreateKey(){
  var dur=$('tk_duration').value;
  var maxd=$('tk_maxdev').value;
  var note=$('tk_note').value;
  var cnt=$('tk_count').value;
  var typ=document.querySelector('input[name="tk_type"]:checked').value;
  showLoad();
  var fd=new FormData();
  fd.append('duration',dur); fd.append('max_devices',maxd); fd.append('note',note);
  fd.append('count',cnt); fd.append('key_type',typ);
  fetch('/api/create_key',{method:'POST',body:fd,credentials:'same-origin'})
  .then(function(r){
    if(r.status===401){hideLoad();alert('Phiên đăng nhập hết hạn!');location.reload();return null;}
    return r.json();
  })
  .then(function(d){
    if(!d) return;
    hideLoad(); toast(d.status==='success',d.status==='success'?'Tạo key thành công!':'Tạo key thất bại');
    var box=$('tk_result');
    box.style.display='block';
    if(d.status==='success'){
      var keys=d.keys||d.key||[];
      if(!Array.isArray(keys))keys=[keys];
      var html='<div class="result-box"><div class="result-title"><i class="fa-solid fa-check-circle" style="color:var(--success);"></i> TẠO THÀNH CÔNG '+keys.length+' KEY</div>';
      keys.forEach(function(k){
        html+='<div style="display:flex;align-items:center;justify-content:space-between;padding:8px 0;border-bottom:1px solid var(--border);">'
          +'<span class="key-val">'+k+'</span>'
          +'<button class="btn-sm btn-sm-blue" onclick="copyText(this.previousElementSibling.textContent)"><i class="fa-solid fa-copy"></i> Sao chép</button>'
          +'</div>';
      });
      box.innerHTML=html+'</div>';
      // Auto-refresh database list after creating keys
      loadDatabase();
    } else {
      box.innerHTML='<div style="background:rgba(239,68,68,0.07);border:1px solid rgba(239,68,68,0.2);border-radius:12px;padding:12px;color:var(--danger);font-size:0.84rem;">'+( d.message||'Lỗi tạo key')+'</div>';
    }
  }).catch(function(e){hideLoad();toast(false,'Lỗi kết nối: '+e.message);});
}

// =============================================
// STATS
// =============================================
function loadStats(){
  showLoad();
  fetch('/api/key_stats',{credentials:'same-origin'})
  .then(function(r){if(r.status===401){hideLoad();alert('Phiên đăng nhập hết hạn!');location.reload();return null;}return r.json();})
  .then(function(d){
    hideLoad();
    var sc=$('statsCards');
    var total=d.total||0,active=d.active||d.activated||0,expired=d.expired||0,pending=d.pending||d.not_activated||0;
    var devTotal=d.total_devices||0;
    var stats=[
      {label:'Tổng Keys',val:total,icon:'key',color:'var(--primary)'},
      {label:'Đã kích hoạt',val:active,icon:'check-circle',color:'var(--success)'},
      {label:'Hết hạn',val:expired,icon:'circle-xmark',color:'var(--danger)'},
      {label:'Chưa kích hoạt',val:pending,icon:'clock',color:'#f59e0b'},
    ];
    sc.innerHTML=stats.map(function(s){
      return '<div class="card" style="padding:16px;margin-bottom:0;">'
        +'<div style="font-size:1.6rem;color:'+s.color+';margin-bottom:6px;"><i class="fa-solid fa-'+s.icon+'"></i></div>'
        +'<div style="font-size:1.4rem;font-weight:900;color:var(--text);">'+s.val+'</div>'
        +'<div style="font-size:0.73rem;color:var(--muted);font-weight:700;text-transform:uppercase;letter-spacing:0.5px;">'+s.label+'</div>'
        +'</div>';
    }).join('');
    // Render recent keys as history
    if(d.keys){
      var rows=d.keys.map(function(h){
        var badge='';
        if(h.status==='Đã kích hoạt')badge='<span class="badge badge-ok" style="font-size:0.6rem;">Active</span>';
        else if(h.status==='Hết hạn')badge='<span class="badge badge-err" style="font-size:0.6rem;">Hết hạn</span>';
        else badge='<span class="badge badge-warn" style="font-size:0.6rem;">Chưa KH</span>';
        return '<tr><td><span class="key-val">'+fmt(h.key)+'</span></td><td>'+badge+'</td><td style="font-size:0.72rem;">'+fmt(h.created_at_str||'—')+'</td><td style="font-size:0.72rem;">'+fmt(h.han_dung||'—')+'</td></tr>';
      }).join('');
      $('histBody').innerHTML=rows||'<tr><td colspan="4" style="text-align:center;color:var(--muted);padding:18px;">Không có dữ liệu</td></tr>';
    }
  }).catch(function(e){hideLoad();toast(false,'Lỗi tải thống kê: '+e.message);});
}

// =============================================
// FREE KEYS
// =============================================
function loadFreeKeys(){
  showLoad();
  fetch('/api/list_free_keys',{credentials:'same-origin'})
  .then(function(r){
    if(r.status===401){hideLoad();alert('Phiên đăng nhập hết hạn!');location.reload();return null;}
    return r.json();
  })
  .then(function(d){
    if(!d){return;}
    hideLoad();
    // Backend trả về array trực tiếp hoặc object {keys:[...]}
    var keys=Array.isArray(d)?d:(d.keys||d.data||[]);
    var _fkArr=keys.map(function(x){return x.key;});
    var html=keys.map(function(k,i){
      return '<tr><td><span class="key-val" onclick="copyText(_fkArr['+i+'])" style="cursor:pointer;">'+k.key+'</span></td>'
        +'<td style="font-size:0.73rem;">'+fmt(k.ip)+'</td>'
        +'<td style="font-size:0.73rem;">'+fmt(k.created_at||k.time)+'</td>'
        +'<td style="font-size:0.73rem;">'+fmt(k.expiry_date||k.expiry)+'</td>'
        +'<td><button class="btn-sm btn-sm-red" onclick="deleteFreeKey(_fkArr['+i+'])"><i class="fa-solid fa-trash"></i></button></td>'
        +'</tr>';
    }).join('');
    $('freeKeyBody').innerHTML=html||'<tr><td colspan="5" style="text-align:center;color:var(--muted);padding:22px;">Không có key free</td></tr>';
  }).catch(function(e){hideLoad();toast(false,'Lỗi tải dữ liệu: '+e.message);});
}

function deleteFreeKey(key){
  if(!confirm('Xóa key '+key+'?'))return;
  showLoad();
  fetch('/delete/'+encodeURIComponent(key),{credentials:'same-origin'})
  .then(function(r){
    if(r.status===401){hideLoad();alert('Phiên đăng nhập hết hạn!');location.reload();return null;}
    return r.json();
  })
  .then(function(d){if(d){hideLoad();toast(d.status==='success');if(d.status==='success')loadFreeKeys();}})
  .catch(function(e){hideLoad();toast(false,'Lỗi kết nối');});
}

// =============================================
// CHANGE PASSWORD
// =============================================
function doChangePass(){
  var o=$('cp_old').value,n=$('cp_new').value,c=$('cp_confirm').value;
  if(!n||!c){alert('Vui lòng điền mật khẩu mới và xác nhận');return;}
  if(n!==c){alert('Mật khẩu mới không khớp');return;}
  if(n.length<6){alert('Mật khẩu mới phải dài ít nhất 6 ký tự');return;}
  // Get current admin username from session — send same username or ask admin to enter it
  var u=prompt('Nhập tài khoản admin mới (mặc định: phedevdzz):','phedevdzz');
  if(!u)return;
  showLoad();
  // Backend /api/change_admin accepts: u=new_user, p=new_pass (verifies via session)
  var fd=new FormData(); fd.append('u',u); fd.append('p',n);
  fetch('/api/change_admin',{method:'POST',body:fd,credentials:'same-origin'})
  .then(function(r){if(r.status===401){hideLoad();alert('Phiên đăng nhập hết hạn! Vui lòng đăng nhập lại.');location.reload();return null;}return r.json();})
  .then(function(d){
    if(!d) return;
    hideLoad(); toast(d.status==='success',d.status==='success'?'Đổi mật khẩu thành công!':'Đổi mật khẩu thất bại');
    var box=$('cp_result'); box.style.display='block';
    if(d.status==='success'){
      box.innerHTML='<div style="background:rgba(34,197,94,0.08);border:1px solid rgba(34,197,94,0.25);border-radius:12px;padding:12px;color:#15803d;font-size:0.84rem;font-weight:700;"><i class="fa-solid fa-check"></i> Đổi mật khẩu thành công!</div>';
      $('cp_old').value=''; $('cp_new').value=''; $('cp_confirm').value='';
    } else {
      box.innerHTML='<div style="background:rgba(239,68,68,0.07);border:1px solid rgba(239,68,68,0.2);border-radius:12px;padding:12px;color:var(--danger);font-size:0.84rem;">'+(d.message||'Lỗi đổi mật khẩu')+'</div>';
    }
  }).catch(function(e){hideLoad();toast(false,'Lỗi kết nối: '+e.message);});
}

// =============================================
// DEVICE REVIEW
// =============================================
function loadDeviceRequests(){
  showLoad();
  Promise.all([
    fetch('/api/list_device_requests',{credentials:'same-origin'}).then(function(r){if(r.status===401){alert('Phiên đăng nhập hết hạn!');location.reload();return [];}return r.json();}),
    fetch('/api/list_approved_devices',{credentials:'same-origin'}).then(function(r){if(r.status===401){return [];}return r.json();})
  ]).then(function(results){
    hideLoad();
    var pending=results[0]||[];
    var approved=results[1]||[];
    renderDevPending(Array.isArray(pending)?pending:(pending.requests||[]));
    renderDevApproved(Array.isArray(approved)?approved:(approved.devices||[]));
  }).catch(function(e){hideLoad();toast(false,'Lỗi kết nối: '+e.message);});
}

var _devPendArr=[];
function renderDevPending(list){
  if(!list.length){$('devReqList').innerHTML='<div class="dev-empty"><i class="fa-solid fa-inbox"></i>Chưa có thiết bị chờ duyệt</div>';return;}
  _devPendArr=list.map(function(r){return {key:r.key,dev:r.device_id};});
  $('devReqList').innerHTML=list.map(function(r,i){
    return '<div class="dev-req-card">'
      +'<div class="dev-req-device"><i class="fa-solid fa-mobile-screen-button" style="color:var(--primary);margin-top:2px;flex-shrink:0;"></i>'+r.device_id+'</div>'
      +'<div class="dev-req-meta"><span><i class="fa-solid fa-key"></i> '+r.key+'</span><span><i class="fa-solid fa-clock"></i> '+fmt(r.time||r.created_at)+'</span></div>'
      +'<div class="dev-req-actions">'
      +'<span class="badge-pending"><i class="fa-solid fa-hourglass"></i> Chờ duyệt</span>'
      +'<button class="btn-sm btn-sm-green" onclick="approveDevice(_devPendArr['+i+'].key,_devPendArr['+i+'].dev)"><i class="fa-solid fa-check"></i> Duyệt</button>'
      +'<button class="btn-sm btn-sm-red" onclick="rejectDevice(_devPendArr['+i+'].key,_devPendArr['+i+'].dev)"><i class="fa-solid fa-ban"></i> Từ chối</button>'
      +'</div></div>';
  }).join('');
}

var _devApvArr=[];
function renderDevApproved(list){
  if(!list.length){$('devApvList').innerHTML='<div class="dev-empty"><i class="fa-solid fa-check-double"></i>Chưa có thiết bị được duyệt</div>';return;}
  _devApvArr=list.map(function(r){return {key:r.key,dev:r.device_id};});
  $('devApvList').innerHTML=list.map(function(r,i){
    return '<div class="apv-card">'
      +'<div class="apv-device">'+r.device_id+'</div>'
      +'<div class="apv-meta"><span><i class="fa-solid fa-key"></i> '+r.key+'</span><span><i class="fa-solid fa-clock"></i> '+fmt(r.approved_at||r.time)+'</span></div>'
      +'<div class="apv-actions">'
      +'<span class="badge-approved-dev"><i class="fa-solid fa-check"></i> Đã duyệt</span>'
      +'<button class="btn-sm btn-sm-red" onclick="revokeDevice(_devApvArr['+i+'].key,_devApvArr['+i+'].dev)"><i class="fa-solid fa-trash"></i> Thu hồi</button>'
      +'</div></div>';
  }).join('');
}

function approveDevice(rid,key,dev){
  showLoad();
  // approve with default 1 month duration
  var fd=new FormData(); fd.append('req_id',rid); fd.append('val','30'); fd.append('unit','ngày');
  fetch('/api/approve_device_request',{method:'POST',body:fd,credentials:'same-origin'})
  .then(function(r){if(r.status===401){hideLoad();alert('Phiên đăng nhập hết hạn!');location.reload();return null;}return r.json();})
  .then(function(d){if(d){hideLoad();toast(d.status==='success');if(d.status==='success')loadDeviceRequests();}})
  .catch(function(e){hideLoad();toast(false,'Lỗi kết nối');});
}

function rejectDevice(rid){
  showLoad();
  var fd=new FormData(); fd.append('req_id',rid);
  fetch('/api/reject_device_request',{method:'POST',body:fd,credentials:'same-origin'})
  .then(function(r){if(r.status===401){hideLoad();alert('Phiên đăng nhập hết hạn!');location.reload();return null;}return r.json();})
  .then(function(d){if(d){hideLoad();toast(d.status==='success');if(d.status==='success')loadDeviceRequests();}})
  .catch(function(e){hideLoad();toast(false,'Lỗi kết nối');});
}

function revokeDevice(deviceId){
  if(!confirm('Thu hồi quyền thiết bị này?'))return;
  showLoad();
  var fd=new FormData(); fd.append('device_id',deviceId);
  fetch('/api/delete_approved_device',{method:'POST',body:fd,credentials:'same-origin'})
  .then(function(r){if(r.status===401){hideLoad();alert('Phiên đăng nhập hết hạn!');location.reload();return null;}return r.json();})
  .then(function(d){if(d){hideLoad();toast(d.status==='success');if(d.status==='success')loadDeviceRequests();}})
  .catch(function(e){hideLoad();toast(false,'Lỗi kết nối');});
}

// =============================================
// CHECK IP (Admin tab)
// =============================================
function doCheckIp(){
  var ip=$('ci_ip').value.trim();
  if(!ip)return;
  showLoad();
  fetch('https://ipapi.co/'+encodeURIComponent(ip)+'/json/')
  .then(function(r){return r.json();})
  .then(function(d){
    hideLoad();
    var box=$('ci_result'); box.style.display='block';
    if(!d.error){
      var html='<div class="check-ip-result" style="display:block;">'
        +'<div class="result-title"><i class="fa-solid fa-globe"></i> Thông Tin IP: '+ip+'</div>'
        +'<div class="ip-info-grid">';
      var fields=[
        ['Quốc gia',(d.country_name||'—')+' '+( d.country_code||'')],
        ['Thành phố',d.city||'—'],
        ['Vùng',d.region||'—'],
        ['ISP / Tổ chức',d.org||d.asn||'—'],
        ['Múi giờ',d.timezone||'—'],
        ['Tọa độ',(d.latitude&&d.longitude)?d.latitude+', '+d.longitude:'—']
      ];
      fields.forEach(function(f,i){
        html+='<div class="ip-info-cell"><div class="ic-label">'+f[0]+'</div><div class="ic-val">'+f[1]+'</div></div>';
      });
      box.innerHTML=html+'</div></div>';
    } else {
      box.innerHTML='<div style="background:rgba(239,68,68,0.07);border:1px solid rgba(239,68,68,0.2);border-radius:12px;padding:12px;color:var(--danger);font-size:0.84rem;">'+(d.reason||'Lỗi kiểm tra IP')+'</div>';
    }
  }).catch(function(){hideLoad();$('ci_result').style.display='block';$('ci_result').innerHTML='<div style="color:var(--danger);padding:10px;">Không thể kiểm tra IP này.</div>';});
}

// =============================================
// GETKEY CONFIG
// =============================================
function loadGetkeyConfig(){
  fetch('/admin/get_free_config',{credentials:'same-origin'})
  .then(function(r){if(r.status===401){return null;}return r.json();})
  .then(function(d){
    if(!d)return;
    // backend returns: val (number), unit (tiếng/ngày), dev (devices)
    if(d.val)$('gkc_duration').value=d.val;
    if(d.dev)$('gkc_maxdev').value=d.dev;
    // unit shown in label
  }).catch(function(){});
}

function saveGetkeyConfig(){
  var dur=$('gkc_duration').value;
  var maxd=$('gkc_maxdev').value;
  // backend expects v=val, u=unit, d=devices
  showLoad();
  var fd=new FormData();
  fd.append('v',dur); fd.append('u','tiếng'); fd.append('d',maxd);
  fetch('/admin/free_setup',{method:'POST',body:fd,credentials:'same-origin'})
  .then(function(r){if(r.status===401){hideLoad();alert('Phiên đăng nhập hết hạn!');location.reload();return null;}return r.json();})
  .then(function(d){
    if(!d){return;}
    hideLoad(); toast(d.status==='success');
    var box=$('gkc_result'); box.style.display='block';
    if(d.status==='success'){
      box.innerHTML='<div style="background:rgba(34,197,94,0.08);border:1px solid rgba(34,197,94,0.25);border-radius:12px;padding:12px;color:#15803d;font-weight:700;font-size:0.84rem;"><i class="fa-solid fa-check"></i> Lưu cấu hình thành công!</div>';
    } else {
      box.innerHTML='<div style="background:rgba(239,68,68,0.07);border:1px solid rgba(239,68,68,0.2);border-radius:12px;padding:12px;color:var(--danger);font-size:0.84rem;">'+(d.message||'Lỗi lưu cấu hình')+'</div>';
    }
  }).catch(function(){hideLoad();toast(false);});
}

// =============================================
// WEB LOG
// =============================================
function loadWebLog(){
  fetch('/api/web_log',{credentials:'same-origin'})
  .then(function(r){if(r.status===401){return [];}return r.json();})
  .then(function(logs){
    var html=logs.map(function(l){
      var mClass='log-method-'+(l.method==='POST'?'post':'get');
      var sClass=l.status>=400?'log-status-err':'log-status-ok';
      return '<div class="log-entry">'
        +'<span class="log-time">'+l.time+'</span>'
        +'<span class="log-method '+mClass+'">'+l.method+'</span>'
        +'<span class="log-path">'+l.path+'</span>'
        +'<span class="log-status '+sClass+'">'+l.status+'</span>'
        +'<span class="log-ip">'+l.ip+'</span>'
        +'</div>';
    }).join('');
    $('webLogList').innerHTML=html||'<div style="text-align:center;padding:24px;color:var(--muted);font-size:0.8rem;">Chưa có log</div>';
  }).catch(function(e){console.warn('loadWebLog error', e);});
}

// =============================================
// API URLS
// =============================================
function loadApiUrls(){
  var base=window.location.origin;
  var paths={ep_create:'/api/create_key',ep_check:'/api/check_key?key=XXXX',ep_validate:'/api/validate'};
  Object.keys(paths).forEach(function(id){
    var el=$(id); if(el)el.textContent=base+paths[id];
  });
}

}

// =============================================
// LOAD FREE CONFIG FOR GETKEY PILLS
// =============================================
(function loadPublicFreeConfig(){
  fetch('/api/free_config')
  .then(function(r){return r.json();})
  .then(function(d){
    if(d && d.val){
      var durEl=$('gkPillDurTxt');
      var devEl=$('gkPillDevTxt');
      if(durEl) durEl.textContent=(d.val+' '+d.unit+' sử dụng');
      if(devEl){
        var devNum=parseInt(d.dev)||1;
        devEl.textContent=(devNum>=9999?'Không giới hạn':(devNum+' thiết bị'));
      }
    }
  }).catch(function(){
    var durEl=$('gkPillDurTxt');
    if(durEl) durEl.textContent='12 giờ sử dụng';
    var devEl=$('gkPillDevTxt');
    if(devEl) devEl.textContent='1 thiết bị';
  });
})();
