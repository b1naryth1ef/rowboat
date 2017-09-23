function notify(level, msg) {
  $(".alert").remove();
  var div = $('<div class="alert alert-' + level + '">' + msg + '</div>');
  $("#page-wrapper").prepend(div);
  div.delay(6000).fadeOut();
}
