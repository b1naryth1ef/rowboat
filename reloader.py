import BaseHTTPServer
import subprocess


SUPERVISOR = None


class BotSupervisor(object):
    def __init__(self):
        self.proc = None
        self.start()

    def start(self):
        self.proc = subprocess.Popen(['python', '-m', 'disco.cli', '--config', 'config.yaml'])

    def stop(self):
        self.proc.terminate()

    def restart(self):
        try:
            self.stop()
        except:
            pass

        self.start()


class RestarterHandler(BaseHTTPServer.BaseHTTPRequestHandler):
    def do_POST(s):
        s.send_response(200)
        s.end_headers()

        subprocess.check_call(['git', 'pull', 'origin', 'master'])
        SUPERVISOR.restart()


if __name__ == '__main__':
    SUPERVISOR = BotSupervisor()
    httpd = BaseHTTPServer.HTTPServer(('0.0.0.0', 8080), RestarterHandler)

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass

    httpd.server_close()
