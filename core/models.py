from sqlalchemy import Column, String, Integer, DateTime, func, ForeignKey, UniqueConstraint, Boolean
from sqlalchemy.orm import relationship, Mapped, mapped_column
from sqlalchemy.ext.declarative import declared_attr
from sqlalchemy.dialects.postgresql import JSON
from typing import Optional
from core.database import Base
import uuid

class Autoid():
    @declared_attr
    def id (cls):
        return Column(
            String,
            primary_key= True,
            default = lambda:str(uuid.uuid4()),
            index = True,
            unique = True,
            nullable = False
        )


class Users(Base, Autoid):
    __tablename__ = "users"
    username = Column(String, nullable = False)
    email = Column(String, nullable = False, unique = True)
    password = Column(String , nullable = False)
    token_version:  Mapped[int] = mapped_column(Integer, nullable= False, default = 1)
    refresh_jti :Mapped[Optional[str]] = mapped_column(String, nullable = True)
    created_at = Column(DateTime(timezone= True), nullable = False, server_default = func.now())
    clinics = relationship("RegisteredClinics", back_populates= "owner", cascade="all, delete")
    dsos = relationship("Dso", back_populates= "user", cascade= "all, delete")
    user_clinic = relationship("UserClinic", back_populates= "users")


class Dso(Base, Autoid):
    __tablename__ = "Dsos"
    name = Column(String, nullable= False)
    user_id = Column(String, ForeignKey("users.id", ondelete="CASCADE"), nullable = False )
    created_at = Column(DateTime(timezone= True), nullable = False, server_default= func.now())
    clinics = relationship("RegisteredClinics", back_populates= "dso", cascade="all, delete")
    user = relationship("Users", back_populates= "dsos")

    __table_args__ = (
        UniqueConstraint("user_id", name = "uq_dso_user_id"),
    )


class RegisteredClinics (Base, Autoid):
    __tablename__ = "registered_clinics"
    crm_type = Column(String, nullable = False)
    clinic_name = Column(String, nullable = False)
    clinic_number = Column(Integer, nullable = False )
    clinic_timezone : Mapped[str] = mapped_column(String, nullable = False)
    od_developer_key = Column(String, nullable = False)
    od_customer_key = Column(String, nullable = False)
    crm_api_key = Column(String, nullable = False)
    location_id = Column(String, nullable = False)
    calendar_id = Column(String, nullable = False )
    operatory_calendar_map = Column(JSON, nullable=True)
    owner_id = Column(String, ForeignKey("users.id", ondelete= "CASCADE"), nullable = False )
    dso_id = Column(String, ForeignKey("Dsos.id", ondelete="CASCADE"), nullable = True)
    created_at = Column(DateTime(timezone= True), nullable = False, server_default= func.now())
    dso = relationship("Dso", back_populates= "clinics")
    owner = relationship("Users", back_populates = "clinics")
    patients = relationship("Patients", back_populates= "clinic", cascade="all, delete")
    appointments = relationship("Appointments", back_populates= "clinic", cascade="all, delete")
    user_clinic = relationship("UserClinic", back_populates= "clinic", cascade="all, delete" )


class Patients(Base, Autoid):
    __tablename__ = "patients"
    FName = Column(String, nullable = True )
    LName = Column(String, nullable = True)
    Gender = Column(String, nullable = False )
    phone = Column(String, nullable = False )
    email = Column(String, nullable = True )
    pat_num : Mapped[int]= mapped_column(Integer, nullable = False )
    contact_id = Column(String, nullable= False)
    clinic_id = Column(String, ForeignKey("registered_clinics.id", ondelete="CASCADE"), nullable = False)
    created_at = Column(DateTime(timezone= True), nullable = False, server_default= func.now())
    clinic = relationship("RegisteredClinics", back_populates = "patients")
    appointments = relationship("Appointments", back_populates="patient", cascade="all, delete")

    __table_args__ = (
        UniqueConstraint ("clinic_id", "pat_num"  , name = "uq_clinic_patnum" ),
        UniqueConstraint ("clinic_id", "contact_id" , name = "uq_clinic_contactid" ) 
                     )



class Appointments(Base, Autoid):
    __tablename__ = "appointments"
    AptNum : Mapped[int] = mapped_column(Integer, nullable = True)
    event_id :Mapped[str] =  mapped_column(String, nullable = False )
    status = Column(String, nullable  = False)
    start_time = Column(String, nullable = False )
    end_time = Column(String, nullable = False)
    date = Column(String , nullable = False)
    calendar_id = Column(String, nullable = False )
    commslog_done : Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    popups_done : Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    clinic_id = Column(String, ForeignKey("registered_clinics.id", ondelete="CASCADE"), nullable = False )
    pat_id = Column (String, ForeignKey("patients.id", ondelete="CASCADE"), nullable = False )
    created_at = Column(DateTime(timezone= True), nullable = False, server_default= func.now())
    clinic = relationship("RegisteredClinics", back_populates= "appointments",  cascade="all, delete")
    patient = relationship("Patients", back_populates= "appointments",  cascade="all, delete")
    
    __table_args__ = (
        UniqueConstraint("clinic_id", "AptNum" , name=  "uq_clinic_AptNum"),
        UniqueConstraint("clinic_id", "event_id" , name = "uq_clinic_eventid" )
                     )


class UserClinic(Base, Autoid):
    __tablename__ = "userclinic"
    role = Column(String, nullable=False)
    user_id  = Column(String, ForeignKey("users.id", ondelete="CASCADE"), nullable = False)
    clinic_id = Column(String, ForeignKey("registered_clinics.id", ondelete= "CASCADE"), nullable= False)
    users = relationship("Users", back_populates="user_clinic",  cascade="all, delete")
    clinic = relationship("RegisteredClinics", back_populates="user_clinic",  cascade="all, delete")




class Audit_logs(Base, Autoid):
    __tablename__ = "audit_logs"
    clinic_id = Column(String, nullable=False)
    entity_type = Column(String, nullable=False)
    entity_id = Column(String, nullable=True)
    action  = Column(String, nullable=False)
    status = Column(String, nullable =False)
    source = Column(String, nullable=False)
    details = Column(JSON, nullable=True)
    created_at = Column(DateTime(timezone= True), nullable = False, server_default= func.now())


