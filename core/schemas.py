from pydantic import BaseModel, EmailStr, ConfigDict, StringConstraints, Field 
from datetime import datetime
from typing import Optional, Literal
from typing import Literal, Annotated, List, Dict
from uuid import UUID


Datestr = Annotated[str, StringConstraints(pattern=r"^\d{4}-\d{2}-\d{2}$")]

class Webhook_requests(BaseModel):
    event_id : str
    contact_id : str 
    commslog: str
    status : str
    Date : str 
    start_time : str
    end_time: str   
    first_name: str 
    last_name : str 
    BirthDate: str 
    Gender: str 
    Notes: Optional[str] = None 
    pop_up: Optional[str] = None 
    calendar_id : str 
    Note : str 
    WirelessPhone:str 
    Email: EmailStr 
    PriProv:str


class webhook_response(BaseModel):
    status: int
    job_id: str
    message: str
    clinic: UUID
    crm_type: str



class patient_model(BaseModel):
    FName: str
    LName: str
    Gender: Optional[str] = None
    Address: Optional[str] = None
    Birthdate: Optional[str] = None
    WirelessPhone: Optional[str] = None
    Email: Optional[EmailStr] = None
    position: Optional[str] = None


class Appointments_create(BaseModel):
    PatNum:int
    Pattern: str 
    AptDateTime : str
    Op: str
    AptStatus: str
    Note: Optional[str] = None

class Appointments_update(  BaseModel):
    Pattern : str 
    AptDateTime : str 
    Op : str 
    AptStatus : str 

class create_commslogs(BaseModel):
    commlogs : str 
    PatNum : int 

class create_pop_ups(BaseModel):
    pop_ups: str 
    PatNum :int 



###########################################  GHL  WORKKS 

class create_contact_ghl(BaseModel):
    firstName: str 
    lastName:str
    email: EmailStr
    phone:str 
    dateOfBirth: Datestr

class create_appointment_ghl(BaseModel):
    calendarId: str 
    locationId: str 
    contactId : str 
    startTime : str 
    endTime : str 
    ignoreFreeSlotValidation : Literal[True]
    assignedUserId : str 
    appointmentStatus : str 
    

class update_appointment_ghl(BaseModel):
    calendarId: str 
    locationId: str  
    startTime : str 
    endTime : str 
    ignoreFreeSlotValidation : Literal[True]
    assignedUserId : str 
    appointmentStatus : str 





################Authentication Schema 
class loginresponse(BaseModel):
    access_token : str

class refreshresponse(BaseModel):
    access_token: str


class loginrequest(BaseModel):
    email : EmailStr
    password: str

class logoutresponse(BaseModel):
    message : str 

##################################UserRegistration and clinic registration 
class usercreate(BaseModel):
    username:str 
    email : EmailStr
    password : str
    username:str 

class userout(BaseModel):
    id : UUID 
    email : EmailStr 
    username : str 

    class config:
        orm_mode = True 

class registerdso(BaseModel):
    name : str 

class dsoout(BaseModel):
    id : UUID 
    name : str 
    
    class config:
        orm_mode = True 

class operatorymap(BaseModel):
    calendar_id : str 
    operatories : List[int]

class cliniccreate(BaseModel):
    crm_type: str
    clinic_name : str
    clinic_number : int 
    clinic_timezone : str 
    od_developer_key : str 
    od_customer_key : str 
    crm_api_key : str 
    location_id : str 
    calendar_id : str 
    operatory_calendar_map : Dict[str, List[operatorymap]] = Field ()  

class clinicout(BaseModel):
    id: UUID 
    clinic_name : str 

    class config:
        orm_mode = True


###########################Appointment###############################
class AppointmentRequest(BaseModel):
    date_str : str
    start_str: str
    end_str : str
    status: str
    calendar_id: str
    event_id: str  
    contact_id : str 
    Note : Optional[str] = None 
    pop_up : Optional[str] = None 
    pat_id : UUID 
    commslog : Optional[str] = None 
    pat_Num : int 
    clinic_timezone:  str 

######  Invite Request
class create_dso_invite_request(BaseModel):
    email : EmailStr
    role: Literal["manager", "staff"]

class create_clinic_invite_request(BaseModel):
    email: EmailStr
    role: Literal["manager", "staff"]

class accept_invite_request(BaseModel):
    token: str

class invite_out(BaseModel):
    message: str
    invite_token: str
    expires_at: str

    class config:
        orm_mode = True

#### webhook details 
class webhook_config_out(BaseModel):
    webhook_url: str
    header_name: str
    header_value: str

#Workspace schemas 
class workspace_item(BaseModel):
    scope_type: Literal["dso", "clinic"]
    role: Literal["admin", "manager", "staff"]
    access_source: Literal["owner", "dso_assignment", "clinic_assignment"]
    dso_id: Optional[UUID] = None
    dso_name: Optional[str] = None
    clinic_id: Optional[UUID] = None
    clinic_name: Optional[str] = None


class workspace_ref(BaseModel):
    scope_type: Literal["dso", "clinic"]
    dso_id: Optional[UUID] = None
    clinic_id: Optional[UUID] = None


class my_workspaces_out(BaseModel):
    user_id: UUID
    workspace_count: int
    workspaces: List[workspace_item]
    default_workspace: Optional[workspace_ref] = None


