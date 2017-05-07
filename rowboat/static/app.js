$('.topbar-noti').click(function (event) {
  event.preventDefault();

  $.post("/notification/ack/" + $(event.currentTarget).attr('id'));
  $(event.currentTarget).remove();
});

function notify(level, msg) {
  $(".alert").remove();
  var div = $('<div class="alert alert-' + level + '">' + msg + '</div>');
  $("#page-wrapper").prepend(div);
  div.delay(6000).fadeOut();
}

Notification.requestPermission().then(function(result) {
  if (Notification.permission === "granted") {
    var source = new EventSource("/notifications/realtime");
    source.onmessage = function (event) {
      var payload = JSON.parse(event.data);
      new Notification(payload.title, {
        body: payload.content,
      });
    }
  }
});
