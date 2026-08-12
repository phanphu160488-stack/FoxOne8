
function doCheck(){
  var k=document.getElementById('cik_key').value.trim();
  var dev=document.getElementById('cik_dev').value.trim();
  if(!k){alert('Vui lòng nhập key');return;}
  document.getElementById('cik_loading').style.display='block';
  document.getElementById('cik_result').style.display='none';
  var url='/api/check_key?key='+encodeURIComponent(k)+(dev?'&device_id='+encodeURIComponent(dev):'');
  fetch(url).then(function(r){return r.json();}).then(function(d){
    document.getElementById('cik_loading').style.display='none';
    var box=document.getElementById('cik_result');
    box.style.display='block';
    if(d.status==='success'){
      var i=d.info||d.data||d;
      var valid=d.valid||d.is_valid;
      var badge=valid?'<span class="badge-ok"><i class="fa-solid fa-check"></i> Còn hạn</span>':'<span class="badge-no"><i class="fa-solid fa-xmark"></i> Hết hạn</span>';
      var devs=(i.devices||[]).map(function(dv){
        return '<div class="dev-item"><div class="dev-id"><i class="fa-solid fa-mobile-screen-button" style="color:var(--primary);font-size:0.75rem;"></i> '+dv.device_id+'</div><div class="dev-meta">Đăng ký: '+( dv.registered_at||'—')+'</div></div>';
      }).join('');
      box.innerHTML='<div class="result-title"><i class="fa-solid fa-circle-check" style="-webkit-text-fill-color:initial;color:var(--success);"></i> KẾT QUẢ</div>'
        +'<div class="info-row"><span class="info-label">Trạng thái</span><span class="info-val">'+badge+'</span></div>'
        +'<div class="info-row"><span class="info-label">Key</span><span class="info-val" style="font-size:0.7rem;font-family:monospace;">'+k+'</span></div>'
        +'<div class="info-row"><span class="info-label">Loại</span><span class="info-val">'+(i.key_type||i.type||'—')+'</span></div>'
        +'<div class="info-row"><span class="info-label">Hết hạn</span><span class="info-val">'+(i.expiry_date||i.expiry||'—')+'</span></div>'
        +'<div class="info-row"><span class="info-label">Thiết bị</span><span class="info-val">'+(i.devices_count!==undefined?i.devices_count:i.used_devices||0)+' / '+(i.max_devices||i.max_dev||'—')+'</span></div>'
        +(devs?'<div style="margin-top:4px;font-size:0.72rem;font-weight:800;color:var(--muted);text-transform:uppercase;letter-spacing:0.5px;margin-bottom:6px;">Thiết bị đã đăng ký</div><div class="dev-list">'+devs+'</div>':'');
    } else {
      box.innerHTML='<div style="text-align:center;padding:16px;"><div style="font-size:1.8rem;color:var(--danger);margin-bottom:10px;"><i class="fa-solid fa-circle-xmark"></i></div><div style="font-weight:800;color:var(--danger);">'+(d.message||'Key không hợp lệ')+'</div></div>';
    }
  }).catch(function(){document.getElementById('cik_loading').style.display='none';});
}
document.getElementById('cik_key').addEventListener('keydown',function(e){if(e.key==='Enter')doCheck();});
