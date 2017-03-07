$('.topbar-noti').click(function (event) {
  event.preventDefault();

  $.post("/notification/ack/" + $(event.currentTarget).attr('id'));
  $(event.currentTarget).remove();
});
