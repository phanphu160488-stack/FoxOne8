
var stepData=[
  {pct:20,delay:600},
  {pct:45,delay:900},
  {pct:70,delay:700},
  {pct:90,delay:500},
];
var si=0;
function setStep(n,ok){
  for(var i=1;i<=4;i++){
    var el=document.getElementById('s'+i);
    var dot=el.querySelector('.step-dot');
    var ic=el.querySelector('i');
    if(i<n){el.className='step-item done';dot.className='step-dot ok';ic.className='fa-solid fa-check';}
    else if(i===n){el.className='step-item active';dot.className='step-dot spinning';ic.className='fa-solid fa-spinner';}
    else{el.className='step-item';dot.className='step-dot waiting';}
  }
}
function setProgress(pct){
  document.getElementById('pf').style.width=pct+'%';
  document.getElementById('pp').textContent=pct+'%';
}
function animSteps(){
  if(si>=4){confirm_bypass();return;}
  setTimeout(function(){
    setStep(si+1,false);
    setProgress(stepData[si].pct);
    si++;
    animSteps();
  },stepData[si]?stepData[si].delay:600);
}
function confirm_bypass(){
  fetch('/api/confirm_bypass',{method:'POST',headers:{'Content-Type':'application/x-www-form-urlencoded'},body:'token='+encodeURIComponent(token)})
  .then(function(r){return r.json();})
  .then(function(d){
    setProgress(100);
    for(var i=1;i<=4;i++){
      var el=document.getElementById('s'+i);
      var dot=el.querySelector('.step-dot');
      var ic=el.querySelector('i');
      el.className='step-item done'; dot.className='step-dot ok'; ic.className='fa-solid fa-check';
    }
    if(d.status==='success'&&d.key){
      document.getElementById('keyVal').textContent=d.key;
      var expText=d.expiry||d.expiry_date||d.duration_label||'—';
      document.getElementById('keyExp').textContent=expText;
      var devNum=parseInt(d.max_devices)||1;
      document.getElementById('keyDev').textContent=(devNum>=9999?'Không giới hạn':devNum+' thiết bị');
      document.getElementById('keyResult').style.display='block';
    } else {
      document.getElementById('errMsg').textContent=d.message||'Xác minh thất bại';
      document.getElementById('errSub').textContent=d.sub||d.detail||'Vui lòng thử lại sau';
      document.getElementById('errBox').style.display='block';
    }
  }).catch(function(e){
    document.getElementById('errMsg').textContent='Lỗi kết nối máy chủ';
    document.getElementById('errSub').textContent=e.message;
    document.getElementById('errBox').style.display='block';
  });
}
function copyKey(){
  var k=document.getElementById('keyVal').textContent;
  navigator.clipboard.writeText(k).catch(function(){
    var a=document.createElement('textarea');a.value=k;document.body.appendChild(a);a.select();document.execCommand('copy');document.body.removeChild(a);
  });
}
setTimeout(animSteps,800);
