import anyio
import random

class ObjLock:

  def __init__(self):
    self.lock = anyio.Lock()
    self.doing_thing = False

  async def handle_packet(self):
    print(f"Thing is done")
    self.doing_thing = False

  async def send_packet(self, id):
    async with self.lock:
      self.doing_thing = True
      print(f"{id} doing thing")

      while self.doing_thing:
        await anyio.sleep(0.01)

  async def task(self, id):
    while True:
      await self.send_packet(id)
      print(f"{id} done")
      # await anyio.sleep(random.random())

async def printer(obj: ObjLock):
  while True:
    if obj.doing_thing:
      print("Thing is being done ext")
      # await anyio.sleep(1)
      print("Thing is done ext")
      await obj.handle_packet()
    await anyio.sleep(0.01)

async def main():
  obj = ObjLock()

  async with anyio.create_task_group() as tg:
    tg.start_soon(obj.task, 0)
    tg.start_soon(obj.task, 1)
    tg.start_soon(obj.task, 2)
    tg.start_soon(obj_thing_doner, obj)

  #object with lock

if __name__ == "__main__":
  anyio.run(main)