
function doRegister(){
  var k=document.getElementById('dr_key').value.trim();
  var dev=document.getElementById('dr_dev').value.trim();
  var name=document.getElementById('dr_name').value.trim();
  if(!k||!dev){alert('Vui lòng nhập Key và Device ID');return;}
  var btn=document.getElementById('regBtn');
  btn.disabled=true;
  btn.innerHTML='<div class="spinner"></div> Đang gửi...';
  var fd=new FormData();
  fd.append('key',k); fd.append('device_id',dev); if(name)fd.append('device_name',name);
  fetch('/dang-ky-thiet-bi',{method:'POST',body:fd})
  .then(function(r){return r.json();})
  .then(function(d){
    btn.disabled=false;
    btn.innerHTML='<i class="fa-solid fa-paper-plane"></i> GỬI YÊU CẦU';
    var box=document.getElementById('drResult');
    box.style.display='block';
    if(d.status==='success'||d.success){
      box.innerHTML='<div class="success-box"><div class="success-icon"><i class="fa-solid fa-circle-check"></i></div><div class="success-title">Gửi yêu cầu thành công!</div><div class="success-msg">Yêu cầu đăng ký thiết bị đã được ghi nhận. Admin sẽ xét duyệt và thông báo qua Telegram.<br><br><strong>Device ID:</strong> '+dev+'</div></div>';
    } else {
      box.innerHTML='<div class="error-box"><div class="error-icon"><i class="fa-solid fa-circle-xmark"></i></div><div class="error-title">Gửi thất bại</div><div class="error-msg">'+(d.message||'Có lỗi xảy ra, vui lòng thử lại')+'</div></div>';
    }
  }).catch(function(){
    btn.disabled=false;
    btn.innerHTML='<i class="fa-solid fa-paper-plane"></i> GỬI YÊU CẦU';
    document.getElementById('drResult').style.display='block';
    document.getElementById('drResult').innerHTML='<div class="error-box"><div class="error-icon"><i class="fa-solid fa-circle-xmark"></i></div><div class="error-title">Lỗi kết nối</div><div class="error-msg">Không thể kết nối máy chủ, vui lòng thử lại.</div></div>';
  });
}
