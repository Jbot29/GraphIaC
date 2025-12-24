from pydantic import BaseModel

from deepdiff import DeepDiff
from typing import Optional, Literal, Dict

class BaseNode(BaseModel):
    g_id: str    

    def create(self,session,G):
        pass

    @property
    def read_id(self) -> Optional[str]:
        return None
    
    @classmethod
    def read(self,session,G,g_id,read_id):
        pass

    def create(self,session,G) -> bool:
        pass
    
    def update(self,session,G):
        pass
    def delete(self,session,G):
        pass
    
    def diff(self,session,G,diff_object):

        if not isinstance(diff_object, self.__class__):
            return False


        return DeepDiff(self.model_dump(), diff_object.model_dump())



    def import_from_provider(self):

        class_name = self.__class__.__name__
        # Build a comma-separated list of key=value pairs using repr(value)
        fields_str = ", ".join(
            f"{k}={repr(v)}"
            for k, v in self.dict().items()
        )
        # Construct something like: MyModel(field1='abc', field2=123)
        return f"{class_name}({fields_str})"    

class BaseEdge(BaseModel):
    #g_id: str
    #source_g_id: str 
    #destination_g_id: str     
    #node_1_g_id: str
    #node_2_g_id: str

    @property
    def source_g_id(self):
        return None
    
    @property
    def destination_g_id(self):
        return None
    
    def read(self,session):
        pass

    def create(self,session,G) -> bool:
        pass

    def update(self,session,G):
        pass
    def delete(self,session,G):
        pass

    def diff(self,session,G,diff_object):

        if not isinstance(diff_object, self.__class__):
            return False


        return DeepDiff(self.model_dump(), diff_object.model_dump())
    
